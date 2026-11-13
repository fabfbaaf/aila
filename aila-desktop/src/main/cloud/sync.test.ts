import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { StoredMessage } from '../../shared/cloud'
import { ApiError, NetworkError, type Change, type SyncPage } from './api'
import { HistoryStore } from './history'
import { SyncEngine, type SocketLike } from './sync'

/** 内存里的假服务器：和真的一样按序号存变更 */
class FakeServer {
  changes: Change[] = []
  offline = false
  uploads = 0
  lastSeq = 0

  addRemote(m: Partial<StoredMessage> & { id: string }, deleted = false): void {
    // 和真服务器一样：同一条消息只留最新状态，序号继续往上加
    this.changes = this.changes.filter((c) => c.data['id'] !== m.id)
    this.changes.push({
      seq: ++this.lastSeq,
      type: 'message',
      data: { role: 'user', content: '', createdAt: new Date().toISOString(), ...m, deleted }
    })
  }

  api = {
    wsUrl: 'ws://fake/ws/sync',
    accessToken: async () => {
      if (this.offline) throw new NetworkError('offline')
      return 'token'
    },
    sync: async (since: number, limit = 200): Promise<SyncPage> => {
      if (this.offline) throw new NetworkError('offline')
      const all = this.changes.filter((c) => c.seq > since)
      const page = all.slice(0, limit)
      return { changes: page, nextSince: page.at(-1)?.seq ?? since, hasMore: all.length > limit, lastSeq: this.lastSeq }
    },
    upload: async (messages: StoredMessage[]) => {
      if (this.offline) throw new NetworkError('offline')
      this.uploads++
      const accepted = messages.map((m) => {
        const existing = this.changes.find((c) => c.data['id'] === m.id)
        if (!existing) this.addRemote(m)
        return { id: m.id, seq: this.changes.find((c) => c.data['id'] === m.id)!.seq }
      })
      return { accepted, lastSeq: this.lastSeq }
    },
    putSetting: async () => ({ seq: 0 }),
    putMemory: async (m: { id: string }) => {
      if (this.goneMemories.has(m.id)) throw new ApiError(410, 'MEMORY_DELETED', '这条记忆已经删掉了')
      return { seq: ++this.lastSeq }
    },
    deleteMemory: async () => ({ seq: ++this.lastSeq })
  }

  goneMemories = new Set<string>()
}

class FakeSocket implements SocketLike {
  handlers: Record<string, ((arg: never) => void)[]> = {}
  sent: string[] = []
  terminated = false
  on(event: string, cb: (arg: never) => void): void {
    ;(this.handlers[event] ??= []).push(cb)
  }
  emit(event: string, arg?: unknown): void {
    for (const cb of this.handlers[event] ?? []) cb(arg as never)
  }
  send(data: string): void {
    this.sent.push(data)
  }
  close(): void {
    this.emit('close')
  }
  terminate(): void {
    this.terminated = true
    this.emit('close')
  }
}

const msg = (id: string, content = id): StoredMessage => ({ id, role: 'user', content, createdAt: new Date().toISOString() })

function setup() {
  const server = new FakeServer()
  const store = new HistoryStore(null)
  const sockets: FakeSocket[] = []
  let lastSeq = 0
  const engine = new SyncEngine({
    api: server.api,
    store,
    getLastSeq: () => lastSeq,
    setLastSeq: (s) => (lastSeq = s),
    openSocket: () => {
      const s = new FakeSocket()
      sockets.push(s)
      return s
    }
  })
  const remote = vi.fn()
  engine.onRemote = remote
  return { server, store, engine, sockets, remote, lastSeq: () => lastSeq }
}

/** 让排队的 Promise 都跑完 */
const settle = async (): Promise<void> => {
  await vi.advanceTimersByTimeAsync(0)
}

beforeEach(() => vi.useFakeTimers())
afterEach(() => vi.useRealTimers())

describe('SyncEngine', () => {
  it('上传本地消息，拿到序号', async () => {
    const { server, store, engine } = setup()
    store.add(msg('a'))
    store.add(msg('b'))
    engine.start()
    await engine.flush()
    expect(store.unsynced()).toHaveLength(0)
    expect(server.changes.map((c) => c.seq)).toEqual([1, 2])
  })

  it('连上时服务端说有更新：拉下来交给界面', async () => {
    const { server, engine, sockets, remote, lastSeq } = setup()
    server.addRemote({ id: 'x', content: '另一台电脑说的' })
    engine.start()
    await settle()
    sockets[0].emit('open')
    sockets[0].emit('message', '{"type":"hello","seq":1}')
    await settle()
    expect(remote).toHaveBeenCalledWith({ added: [expect.objectContaining({ id: 'x', seq: 1 })], removed: [], settings: [] })
    expect(lastSeq()).toBe(1)
  })

  it('自己上传的消息被推回来时不当成新消息', async () => {
    const { store, engine, sockets, remote } = setup()
    engine.start()
    await settle()
    sockets[0].emit('open')
    store.add(msg('mine'))
    await engine.flush()
    sockets[0].emit('message', '{"type":"changed","seq":1}')
    await settle()
    expect(remote).not.toHaveBeenCalled()
  })

  it('分页拉取直到拉完', async () => {
    const { server, engine, store, lastSeq } = setup()
    for (let i = 0; i < 450; i++) server.addRemote({ id: `m${i}` })
    engine.start()
    await engine.pull()
    expect(store.visible()).toHaveLength(450)
    expect(lastSeq()).toBe(450)
  })

  it('别的设备删掉了消息', async () => {
    const { server, engine, store, remote } = setup()
    server.addRemote({ id: 'gone', content: '说错了' })
    engine.start()
    await engine.pull()
    server.addRemote({ id: 'gone' }, true)
    await engine.pull()
    expect(remote).toHaveBeenLastCalledWith({ added: [], removed: ['gone'], settings: [] })
    expect(store.visible()).toHaveLength(0)
  })

  it('断网时留在本地，重连后补传', async () => {
    const { server, store, engine, sockets } = setup()
    engine.start()
    await settle()
    sockets[0].emit('open')
    server.offline = true
    store.add(msg('offline-1'))
    store.add(msg('offline-2'))
    await engine.flush()
    expect(store.unsynced()).toHaveLength(2)
    expect(engine.status).toBe('offline')

    sockets[0].emit('close') // 连接也断了
    server.offline = false
    await vi.advanceTimersByTimeAsync(1100) // 第一次重连在 0.5–1 秒后
    expect(sockets).toHaveLength(2)
    sockets[1].emit('open')
    await settle()
    expect(store.unsynced()).toHaveLength(0)
    expect(engine.status).toBe('online')
  })

  it('重连间隔逐渐变长，最长 30 秒', async () => {
    const { server, engine } = setup()
    server.offline = true
    const spy = vi.spyOn(server.api, 'accessToken')
    engine.start()
    await vi.advanceTimersByTimeAsync(5 * 60_000)
    // 1+2+4+8+16 秒之后都是 30 秒一次：5 分钟大约十几次，而不是几百次
    expect(spy.mock.calls.length).toBeGreaterThan(8)
    expect(spy.mock.calls.length).toBeLessThan(25)
  })

  it('心跳没有回应就断开重连', async () => {
    const { engine, sockets } = setup()
    engine.start()
    await settle()
    sockets[0].emit('open')
    await vi.advanceTimersByTimeAsync(25_000)
    expect(sockets[0].sent).toContain('{"type":"ping"}')
    sockets[0].emit('message', '{"type":"pong"}')
    await vi.advanceTimersByTimeAsync(25_000 + 10_000)
    expect(sockets[0].terminated).toBe(true) // 第二次 ping 没有回应
    await vi.advanceTimersByTimeAsync(1100)
    expect(sockets).toHaveLength(2)
  })

  it('同时来很多次通知，只拉一次（加一次补拉）', async () => {
    const { server, engine } = setup()
    const spy = vi.spyOn(server.api, 'sync')
    server.addRemote({ id: 'a' })
    engine.start()
    await Promise.all([engine.pull(), engine.pull(), engine.pull(), engine.pull()])
    expect(spy.mock.calls.length).toBeLessThanOrEqual(2)
  })
})

describe('HistoryStore', () => {
  it('按时间排序，重复的 id 不加', () => {
    const store = new HistoryStore(null)
    store.add({ ...msg('b'), createdAt: '2026-01-01T00:00:02Z' })
    store.add({ ...msg('a'), createdAt: '2026-01-01T00:00:01Z' })
    expect(store.add(msg('a'))).toBe(false)
    expect(store.visible().map((m) => m.id)).toEqual(['a', 'b'])
  })

  it('云端的旧变更不会覆盖新状态', () => {
    const store = new HistoryStore(null)
    store.applyRemote({ ...msg('a'), seq: 5, deleted: true })
    expect(store.visible()).toHaveLength(0) // 从没见过、已经删了的不加
    store.applyRemote({ ...msg('b'), seq: 3 })
    expect(store.applyRemote({ ...msg('b'), seq: 2, deleted: true })).toBe('unchanged')
    expect(store.visible()).toHaveLength(1)
  })
})
