import type { RemoteChanges, StoredMemory, StoredMessage, SyncStatus } from '../../shared/cloud'
import { ApiError, NetworkError, type CloudApi, type Change } from './api'
import type { HistoryStore } from './history'
import type { MemoryStore } from './memories'

/** ws 库和测试里的假连接都满足这个接口 */
export interface SocketLike {
  on(event: 'open', cb: () => void): void
  on(event: 'message', cb: (data: unknown) => void): void
  on(event: 'close', cb: () => void): void
  on(event: 'error', cb: (err: Error) => void): void
  send(data: string): void
  close(): void
  terminate(): void
}

export interface SyncDeps {
  api: Pick<CloudApi, 'sync' | 'upload' | 'putSetting' | 'putMemory' | 'deleteMemory' | 'accessToken' | 'wsUrl'>
  store: HistoryStore
  memories?: MemoryStore
  getLastSeq(): number
  setLastSeq(seq: number): void
  openSocket(url: string, token: string): SocketLike
}

const UPLOAD_BATCH = 50
const PULL_PAGE = 200
const FLUSH_DELAY_MS = 300
const HEARTBEAT_MS = 25_000
const PONG_TIMEOUT_MS = 10_000
const RECONNECT_MIN_MS = 1000
const RECONNECT_MAX_MS = 30_000

/**
 * 同步引擎：本机新说的话上传，别的设备说的话拉下来。
 *
 * - 上传：消息先进本地记录（没有 seq 就是没传上去），攒一小会儿批量上传。
 *   断网时留在本地，连上后补传；消息 id 是本地生成的 UUID，重传不会重复。
 * - 拉取：WebSocket 只告诉我们「最新序号是 N」，比本地记的大就按 since 去拉。
 *   推送丢了也不怕：每次重连服务端都会先发 hello（带最新序号），对一下就知道漏没漏。
 * - 连接：断了按 1、2、4……30 秒退避重连（带随机抖动，避免服务端重启后所有客户端同时涌上来）；
 *   每 25 秒发一次心跳，10 秒没回应就认为连接已经死了（比如电脑睡眠后），主动断开重连。
 */
export class SyncEngine {
  onRemote: (changes: RemoteChanges) => void = () => {}
  onStatus: (status: SyncStatus) => void = () => {}
  onSessionProblem: (err: ApiError) => void = () => {}

  status: SyncStatus = 'connecting'
  private running = false
  private socket: SocketLike | null = null
  private reconnectMs = RECONNECT_MIN_MS
  private timers = new Set<ReturnType<typeof setTimeout>>()
  private heartbeat: ReturnType<typeof setInterval> | null = null
  private pongDeadline: ReturnType<typeof setTimeout> | null = null
  private flushing: Promise<void> | null = null
  private flushAgain = false
  private pulling: Promise<void> | null = null
  private pullAgain = false
  private pendingSettings = new Map<string, unknown>()

  constructor(private deps: SyncDeps) {}

  start(): void {
    if (this.running) return
    this.running = true
    void this.connect()
  }

  stop(): void {
    this.running = false
    for (const t of this.timers) clearTimeout(t)
    this.timers.clear()
    this.stopHeartbeat()
    const s = this.socket
    this.socket = null
    s?.close()
  }

  /** 本地有新消息了：稍等一下（可能马上还有一条）再批量上传 */
  localChanged(): void {
    this.later(FLUSH_DELAY_MS, () => void this.flush())
  }

  putSetting(key: string, value: unknown): void {
    this.pendingSettings.set(key, value)
    this.localChanged()
  }

  /** 上传所有没传上去的。同一时间只有一个上传在进行，期间来的新消息等这轮结束再传 */
  flush(): Promise<void> {
    if (this.flushing) {
      this.flushAgain = true
      return this.flushing
    }
    this.flushing = (async () => {
      try {
        do {
          this.flushAgain = false
          await this.uploadAll()
        } while (this.flushAgain && this.running)
      } catch (err) {
        this.handleError(err)
      } finally {
        this.flushing = null
      }
    })()
    return this.flushing
  }

  /** 拉取上次同步之后的所有变化 */
  pull(): Promise<void> {
    if (this.pulling) {
      this.pullAgain = true
      return this.pulling
    }
    this.pulling = (async () => {
      try {
        do {
          this.pullAgain = false
          await this.pullAll()
        } while (this.pullAgain && this.running)
      } catch (err) {
        this.handleError(err)
      } finally {
        this.pulling = null
      }
    })()
    return this.pulling
  }

  // ---------- 上传和拉取 ----------

  private async uploadAll(): Promise<void> {
    for (const [key, value] of [...this.pendingSettings]) {
      await this.deps.api.putSetting(key, value)
      if (this.pendingSettings.get(key) === value) this.pendingSettings.delete(key)
    }
    await this.uploadMemories()
    let pending = this.deps.store.unsynced()
    while (pending.length > 0 && this.running) {
      const batch = pending.slice(0, UPLOAD_BATCH)
      try {
        const res = await this.deps.api.upload(batch)
        for (const a of res.accepted) this.deps.store.markSynced(a.id, a.seq)
      } catch (err) {
        if (err instanceof ApiError && err.status === 400) {
          // 服务端不收（比如内容超长）：只留在本地，别让它卡住后面的消息
          console.warn('[sync] 有消息被服务端拒绝，只保存在本地：', err.message)
          for (const m of batch) this.deps.store.markSynced(m.id, -1)
        } else {
          throw err
        }
      }
      pending = this.deps.store.unsynced()
    }
  }

  /** 记忆一条一条传（数量少，改得也不频繁） */
  private async uploadMemories(): Promise<void> {
    const store = this.deps.memories
    if (!store) return
    for (const m of store.dirty()) {
      if (!this.running) return
      try {
        const res = m.deleted ? await this.deps.api.deleteMemory(m.id) : await this.deps.api.putMemory(m)
        store.markSynced(m.id, res.seq)
      } catch (err) {
        if (!(err instanceof ApiError)) throw err
        if (err.status === 410) store.markGone(m.id) // 别的电脑已经删了：删除优先，放弃本地修改
        else if (err.status === 404 && m.deleted) store.markSynced(m.id, 0) // 云端本来就没有
        else if (err.status === 400) store.markSynced(m.id, -1) // 云端不收，只留在本地
        else throw err
      }
    }
  }

  private async pullAll(): Promise<void> {
    const changes: RemoteChanges = { added: [], removed: [], settings: [] }
    let page
    do {
      page = await this.deps.api.sync(this.deps.getLastSeq(), PULL_PAGE)
      for (const c of page.changes) this.apply(c, changes)
      this.deps.setLastSeq(page.nextSince)
    } while (page.hasMore && this.running)
    if (changes.added.length || changes.removed.length || changes.settings.length || changes.memories) this.onRemote(changes)
  }

  private apply(c: Change, out: RemoteChanges): void {
    if (c.type === 'message') {
      const d = c.data as unknown as StoredMessage
      const m = { id: d.id, role: d.role, content: d.content, createdAt: d.createdAt, deleted: d.deleted, seq: c.seq }
      const result = this.deps.store.applyRemote(m)
      if (result === 'added') out.added.push(m)
      else if (result === 'removed') out.removed.push(m.id)
    } else if (c.type === 'setting') {
      const d = c.data as { key: string; value: unknown; deleted: boolean }
      if (this.pendingSettings.has(d.key)) return // 本地刚改过、还没传上去，以本地为准
      out.settings.push({ key: d.key, value: d.value, deleted: d.deleted })
    } else if (c.type === 'memory' && this.deps.memories) {
      const d = c.data as unknown as StoredMemory
      if (this.deps.memories.applyRemote({ ...d, seq: c.seq })) out.memories = true
    }
  }

  // ---------- 连接 ----------

  private async connect(): Promise<void> {
    if (!this.running) return
    this.setStatus('connecting')
    let token: string
    try {
      token = await this.deps.api.accessToken()
    } catch (err) {
      this.handleError(err)
      this.scheduleReconnect()
      return
    }
    if (!this.running) return

    const socket = this.deps.openSocket(this.deps.api.wsUrl, token)
    this.socket = socket
    socket.on('open', () => {
      if (this.socket !== socket) return
      this.reconnectMs = RECONNECT_MIN_MS
      this.setStatus('online')
      this.startHeartbeat(socket)
      void this.flush() // 断线期间攒下的消息
    })
    socket.on('message', (data) => {
      if (this.socket !== socket) return
      this.onSocketMessage(String(data))
    })
    socket.on('error', () => {
      // close 事件随后会到，在那里处理
    })
    socket.on('close', () => {
      if (this.socket !== socket) return
      this.socket = null
      this.stopHeartbeat()
      if (!this.running) return
      this.setStatus('offline')
      this.scheduleReconnect()
    })
  }

  private onSocketMessage(text: string): void {
    let msg: { type?: string; seq?: number }
    try {
      msg = JSON.parse(text)
    } catch {
      return
    }
    if (msg.type === 'pong') {
      if (this.pongDeadline) clearTimeout(this.pongDeadline)
      this.pongDeadline = null
    } else if ((msg.type === 'hello' || msg.type === 'changed') && typeof msg.seq === 'number') {
      if (msg.seq > this.deps.getLastSeq()) void this.pull()
    }
  }

  private startHeartbeat(socket: SocketLike): void {
    this.stopHeartbeat()
    this.heartbeat = setInterval(() => {
      socket.send('{"type":"ping"}')
      if (this.pongDeadline) return
      this.pongDeadline = setTimeout(() => {
        // 心跳没有回应：连接已经死了（网络断了、电脑睡眠过），不等 TCP 超时，直接断开重连
        this.pongDeadline = null
        socket.terminate()
      }, PONG_TIMEOUT_MS)
    }, HEARTBEAT_MS)
  }

  private stopHeartbeat(): void {
    if (this.heartbeat) clearInterval(this.heartbeat)
    if (this.pongDeadline) clearTimeout(this.pongDeadline)
    this.heartbeat = null
    this.pongDeadline = null
  }

  private scheduleReconnect(): void {
    if (!this.running) return
    const jitter = this.reconnectMs * (0.5 + Math.random() * 0.5)
    this.reconnectMs = Math.min(this.reconnectMs * 2, RECONNECT_MAX_MS)
    this.later(jitter, () => void this.connect())
  }

  private handleError(err: unknown): void {
    if (err instanceof ApiError && err.status === 401) {
      this.onSessionProblem(err)
    } else if (err instanceof NetworkError) {
      this.setStatus('offline')
    } else {
      console.warn('[sync]', err)
    }
  }

  private setStatus(status: SyncStatus): void {
    if (this.status === status) return
    this.status = status
    this.onStatus(status)
  }

  private later(ms: number, fn: () => void): void {
    const t = setTimeout(() => {
      this.timers.delete(t)
      if (this.running) fn()
    }, ms)
    this.timers.add(t)
  }
}
