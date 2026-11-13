import type { DeviceInfo, StoredMemory, StoredMessage } from '../../shared/cloud'

export interface TokenResponse {
  accessToken: string
  expiresIn: number
  refreshToken: string
  user: { id: number; email: string; displayName: string }
}

export interface Change {
  seq: number
  type: 'message' | 'setting' | 'memory'
  data: Record<string, unknown>
}

export interface SyncPage {
  changes: Change[]
  nextSince: number
  hasMore: boolean
  lastSeq: number
}

export interface MeResponse {
  user: TokenResponse['user']
  currentDeviceId: string
  devices: DeviceInfo[]
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string
  ) {
    super(message)
  }
}

/** 连不上服务器（不是服务器返回了错误） */
export class NetworkError extends Error {}

interface Session {
  accessToken: string
  /** 毫秒时间戳 */
  accessExpiresAt: number
  refreshToken: string
}

/** 访问令牌快过期时提前这么久刷新，免得请求发到一半过期 */
const REFRESH_AHEAD_MS = 60_000
const TIMEOUT_MS = 15_000

/**
 * 云端接口。负责带上访问令牌，过期了自动用刷新令牌换新的。
 *
 * 刷新是「单飞」的：同时有好几个请求发现令牌过期，也只发一次刷新请求。
 * 这很重要——服务端的刷新令牌只能用一次，并发刷新会被当成令牌被盗，整个登录作废。
 */
export class CloudApi {
  /** 换到了新的刷新令牌（要存起来） */
  onTokens: (t: TokenResponse) => void = () => {}
  /** 刷新令牌失效了，只能重新登录 */
  onSessionLost: (reason: string) => void = () => {}

  private session: Session | null = null
  private refreshing: Promise<void> | null = null

  constructor(
    readonly baseUrl: string,
    refreshToken: string | null = null
  ) {
    if (refreshToken) this.session = { accessToken: '', accessExpiresAt: 0, refreshToken }
  }

  get signedIn(): boolean {
    return this.session !== null
  }

  get wsUrl(): string {
    return this.baseUrl.replace(/^http/, 'ws') + '/ws/sync'
  }

  // ---------- 账号 ----------

  async register(body: { email: string; password: string; displayName?: string; deviceId: string; deviceName: string }): Promise<TokenResponse> {
    return this.start(await this.request<TokenResponse>('POST', '/api/auth/register', body, false))
  }

  async login(body: { email: string; password: string; deviceId: string; deviceName: string }): Promise<TokenResponse> {
    return this.start(await this.request<TokenResponse>('POST', '/api/auth/login', body, false))
  }

  async logout(): Promise<void> {
    const refreshToken = this.session?.refreshToken
    this.session = null
    if (refreshToken) await this.request('POST', '/api/auth/logout', { refreshToken }, false)
  }

  me(): Promise<MeResponse> {
    return this.request('GET', '/api/me')
  }

  // ---------- 同步 ----------

  sync(since: number, limit = 200): Promise<SyncPage> {
    return this.request('GET', `/api/sync?since=${since}&limit=${limit}`)
  }

  upload(messages: StoredMessage[]): Promise<{ accepted: { id: string; seq: number }[]; lastSeq: number }> {
    const body = messages.map(({ id, role, content, createdAt }) => ({ id, role, content, createdAt }))
    return this.request('POST', '/api/messages', { messages: body })
  }

  putSetting(key: string, value: unknown): Promise<{ seq: number }> {
    return this.request('PUT', `/api/settings/${encodeURIComponent(key)}`, { value })
  }

  putMemory(m: StoredMemory): Promise<{ seq: number }> {
    const { content, kind, expiresAt, createdAt } = m
    return this.request('PUT', `/api/memories/${encodeURIComponent(m.id)}`, { content, kind, expiresAt: expiresAt ?? null, createdAt })
  }

  deleteMemory(id: string): Promise<{ seq: number }> {
    return this.request('DELETE', `/api/memories/${encodeURIComponent(id)}`)
  }

  /** 有效的访问令牌（快过期时先刷新） */
  async accessToken(): Promise<string> {
    if (!this.session) throw new ApiError(401, 'SIGNED_OUT', '还没有登录')
    if (Date.now() > this.session.accessExpiresAt - REFRESH_AHEAD_MS) await this.refresh()
    return this.session!.accessToken
  }

  // ---------- 内部 ----------

  private start(t: TokenResponse): TokenResponse {
    this.setSession(t)
    return t
  }

  private setSession(t: TokenResponse): void {
    this.session = { accessToken: t.accessToken, accessExpiresAt: Date.now() + t.expiresIn * 1000, refreshToken: t.refreshToken }
    this.onTokens(t)
  }

  private refresh(): Promise<void> {
    if (this.refreshing) return this.refreshing
    const refreshToken = this.session?.refreshToken
    if (!refreshToken) return Promise.reject(new ApiError(401, 'SIGNED_OUT', '还没有登录'))
    this.refreshing = (async () => {
      try {
        this.setSession(await this.request<TokenResponse>('POST', '/api/auth/refresh', { refreshToken }, false))
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) {
          this.session = null
          this.onSessionLost(err.message)
        }
        throw err
      } finally {
        this.refreshing = null
      }
    })()
    return this.refreshing
  }

  private async request<T>(method: string, path: string, body?: unknown, auth = true): Promise<T> {
    const send = async (): Promise<Response> => {
      const headers: Record<string, string> = { accept: 'application/json' }
      if (body !== undefined) headers['content-type'] = 'application/json'
      if (auth) headers['authorization'] = `Bearer ${await this.accessToken()}`
      try {
        return await fetch(this.baseUrl + path, {
          method,
          headers,
          body: body === undefined ? undefined : JSON.stringify(body),
          signal: AbortSignal.timeout(TIMEOUT_MS)
        })
      } catch (err) {
        throw new NetworkError(err instanceof Error ? err.message : String(err))
      }
    }

    let res = await send()
    if (auth && res.status === 401 && this.session) {
      // 访问令牌被服务端拒绝（比如服务端重启换了密钥）：刷新一次再试
      this.session.accessExpiresAt = 0
      res = await send()
    }
    if (res.status === 204) return undefined as T
    const data = (await res.json().catch(() => ({}))) as { code?: string; message?: string }
    if (!res.ok) throw new ApiError(res.status, data.code ?? `HTTP_${res.status}`, data.message ?? `服务器返回 ${res.status}`)
    return data as T
  }
}
