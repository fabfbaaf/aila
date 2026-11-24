import { randomUUID } from 'crypto'
import { mkdirSync, readFileSync, writeFileSync } from 'fs'
import { hostname } from 'os'
import { dirname, join } from 'path'
import {
  DEFAULT_SERVER_URL,
  SKILLS_SETTING,
  type SkillOverrides,
  type AccountStatus,
  type AuthForm,
  type AuthResult,
  type MemoryOp,
  type RemoteChanges,
  type StoredMemory,
  type StoredMessage,
  type SyncStatus
} from '../../shared/cloud'
import { ApiError, CloudApi, NetworkError, type TokenResponse } from './api'
import { HistoryStore } from './history'
import { forAi, MemoryKeeper, type ExtractRequest, type MemoryForAi } from './keeper'
import { MemoryStore } from './memories'
import { SyncEngine, type SocketLike } from './sync'
import { tm } from '../lang'

/** 刷新令牌加密保存（Windows 上是 DPAPI，只有当前系统用户能解开） */
export interface SecretBox {
  encrypt(text: string): string
  decrypt(data: string): string
}

interface SavedState {
  serverUrl: string
  deviceId: string
  /** 技能包开关（没登录也能改，登录后跟着账号同步） */
  skills?: SkillOverrides
  account?: {
    email: string
    displayName: string
    refreshToken: string
    lastSeq: number
  }
}

export interface AccountDeps {
  dataDir: string
  box: SecretBox
  openSocket(url: string, token: string): SocketLike
  onStatus(status: AccountStatus): void
  onRemote(changes: RemoteChanges): void
  /** 记忆列表变了（界面刷新） */
  onMemories(list: StoredMemory[]): void
  /** 请本地 AI 服务整理记忆 */
  extractMemories(req: ExtractRequest): Promise<MemoryOp[]>
}

/**
 * 账号和同步的总入口（主进程）。没登录时只管本地记录；登录后启动同步引擎。
 * 退出登录时清空本机的对话记录：云端还有，换个账号登录也不会把上一个人的记录传过去。
 */
export class Account {
  readonly history: HistoryStore
  readonly memories: MemoryStore
  private keeper: MemoryKeeper
  private state: SavedState
  private api: CloudApi | null = null
  private engine: SyncEngine | null = null
  private stateFile: string

  constructor(private deps: AccountDeps) {
    this.stateFile = join(deps.dataDir, 'cloud.json')
    this.history = new HistoryStore(join(deps.dataDir, 'history.json'))
    this.history.load()
    this.memories = new MemoryStore(join(deps.dataDir, 'memories.json'))
    this.memories.load()
    this.keeper = new MemoryKeeper({
      history: this.history,
      memories: this.memories,
      extract: deps.extractMemories,
      onChanged: () => this.memoriesChanged()
    })
    this.state = this.loadState()
  }

  /** 每次对话带给 AI 服务的记忆（它会挑出相关的几条） */
  memoriesForChat(): MemoryForAi[] {
    return forAi(this.memories.list())
  }

  skillOverrides(): SkillOverrides {
    return { ...(this.state.skills ?? {}) }
  }

  setSkill(id: string, on: boolean): void {
    this.state.skills = { ...(this.state.skills ?? {}), [id]: on }
    this.saveState()
    this.engine?.putSetting(SKILLS_SETTING, this.state.skills)
  }

  deleteMemory(id: string): void {
    if (this.memories.delete(id)) this.memoriesChanged()
  }

  /** 你在记忆页自己加的、改的（和她整理出来的一样同步到别的设备、推给 AI 服务） */
  editMemory(op: MemoryOp): boolean {
    if (op.op === 'update' && !this.memories.list().some((m) => m.id === op.id)) return false
    if (this.memories.apply([op])) this.memoriesChanged()
    return true
  }

  forgetEverything(): number {
    const n = this.memories.deleteAll()
    if (n) this.memoriesChanged()
    return n
  }

  /** 启动时：上次登录着就直接开始同步 */
  start(): void {
    const a = this.state.account
    if (!a) return
    let refreshToken: string
    try {
      refreshToken = this.deps.box.decrypt(a.refreshToken)
    } catch {
      this.signOutLocally() // 换了系统用户或密钥坏了，解不开就当没登录
      return
    }
    this.startSync(new CloudApi(this.state.serverUrl, refreshToken))
  }

  status(): AccountStatus {
    const a = this.state.account
    return {
      serverUrl: this.state.serverUrl,
      status: a ? (this.engine?.status ?? 'connecting') : 'signed-out',
      email: a?.email,
      displayName: a?.displayName,
      pending: this.history.unsynced().length,
      currentDeviceId: this.state.deviceId
    }
  }

  /** 状态 + 设备列表（要请求服务器，慢一点） */
  async details(): Promise<AccountStatus> {
    const base = this.status()
    if (!this.api) return base
    try {
      const me = await this.api.me()
      return { ...base, devices: me.devices }
    } catch {
      return base
    }
  }

  async authenticate(form: AuthForm): Promise<AuthResult> {
    const serverUrl = form.serverUrl.trim().replace(/\/+$/, '') || DEFAULT_SERVER_URL
    const api = new CloudApi(serverUrl)
    const device = { deviceId: this.state.deviceId, deviceName: deviceName() }
    let t: TokenResponse
    try {
      t =
        form.mode === 'register'
          ? await api.register({ email: form.email, password: form.password, displayName: form.displayName, ...device })
          : await api.login({ email: form.email, password: form.password, ...device })
    } catch (err) {
      return { ok: false, error: describe(err, serverUrl) }
    }
    this.stopSync()
    this.state = {
      serverUrl,
      deviceId: this.state.deviceId,
      skills: this.state.skills,
      account: { email: t.user.email, displayName: t.user.displayName, refreshToken: this.deps.box.encrypt(t.refreshToken), lastSeq: 0 }
    }
    this.saveState()
    // 登录前在这台电脑上的聊天记录也传上去，并入这个账号
    this.startSync(api)
    return { ok: true }
  }

  async logout(): Promise<void> {
    const api = this.api
    this.signOutLocally()
    try {
      await api?.logout() // 通知服务端作废令牌；连不上也没关系，本地已经清掉了
    } catch {
      // 忽略
    }
  }

  /** 本机说了一句话。她说完一轮后，顺便整理一下记忆 */
  append(m: StoredMessage): void {
    if (this.history.add(m)) this.engine?.localChanged()
    if (m.role === 'assistant') this.keeper.afterReply()
    this.notify()
  }

  putSetting(key: string, value: unknown): void {
    this.engine?.putSetting(key, value)
  }

  /** 退出程序前：把还没写盘的记录写下去 */
  shutdown(): void {
    this.keeper.stop()
    this.stopSync()
    this.history.saveNow()
    this.memories.saveNow()
  }

  // ---------- 内部 ----------

  private startSync(api: CloudApi): void {
    this.api = api
    api.onTokens = (t) => this.saveRefreshToken(t.refreshToken)
    api.onSessionLost = () => this.signOutLocally()
    const engine = new SyncEngine({
      api,
      store: this.history,
      memories: this.memories,
      getLastSeq: () => this.state.account?.lastSeq ?? 0,
      setLastSeq: (seq) => {
        if (this.state.account && seq !== this.state.account.lastSeq) {
          this.state.account.lastSeq = seq
          this.saveState()
        }
      },
      openSocket: this.deps.openSocket
    })
    engine.onStatus = () => this.notify()
    engine.onRemote = (changes) => {
      for (const s of changes.settings) {
        if (s.key === SKILLS_SETTING && !s.deleted && s.value && typeof s.value === 'object') {
          this.state.skills = s.value as SkillOverrides // 别的电脑改了技能包开关
          this.saveState()
        }
      }
      this.deps.onRemote(changes)
      if (changes.memories) this.deps.onMemories(this.memories.list())
      this.notify()
    }
    engine.onSessionProblem = (err) => {
      if (err.code !== 'SIGNED_OUT') console.warn('[sync] 登录失效：', err.message)
    }
    this.engine = engine
    engine.start()
    void engine.flush()
    this.notify()
  }

  private stopSync(): void {
    this.engine?.stop()
    this.engine = null
    this.api = null
  }

  private signOutLocally(): void {
    this.stopSync()
    this.state = { serverUrl: this.state.serverUrl, deviceId: this.state.deviceId, skills: this.state.skills }
    this.saveState()
    this.history.clear()
    this.memories.clear()
    this.deps.onRemote({ added: [], removed: ['*'], settings: [] }) // 「*」：界面把记录全部清掉
    this.deps.onMemories([])
    this.notify()
  }

  private memoriesChanged(): void {
    this.engine?.localChanged()
    this.deps.onMemories(this.memories.list())
  }

  private saveRefreshToken(token: string): void {
    if (!this.state.account) return
    this.state.account.refreshToken = this.deps.box.encrypt(token)
    this.saveState()
  }

  private notify(): void {
    this.deps.onStatus(this.status())
  }

  private loadState(): SavedState {
    try {
      const s = JSON.parse(readFileSync(this.stateFile, 'utf-8')) as SavedState
      if (s.deviceId) return s
    } catch {
      // 第一次运行
    }
    const fresh: SavedState = { serverUrl: DEFAULT_SERVER_URL, deviceId: randomUUID() }
    this.state = fresh
    this.saveState()
    return fresh
  }

  private saveState(): void {
    mkdirSync(dirname(this.stateFile), { recursive: true })
    writeFileSync(this.stateFile, JSON.stringify(this.state, null, 2), 'utf-8')
  }
}

function deviceName(): string {
  return hostname().slice(0, 100) || tm('我的电脑')
}

function describe(err: unknown, serverUrl: string): string {
  if (err instanceof ApiError) return err.message
  if (err instanceof NetworkError) return tm('连不上同步服务器（{url}）', { url: serverUrl })
  return tm('出了点问题，请稍后再试')
}

export type { SyncStatus }
