import type { MemoryOp, StoredMemory } from '../../shared/cloud'
import type { HistoryStore } from './history'
import type { MemoryStore } from './memories'

/** 发给 AI 服务的记忆（不带同步用的字段） */
export interface MemoryForAi {
  id: string
  content: string
  kind: string
  expiresAt: string | null
  updatedAt: string
}

export interface ExtractRequest {
  messages: { role: 'user' | 'assistant'; content: string; id: string; createdAt: string }[]
  memories: MemoryForAi[]
}

export interface KeeperDeps {
  history: HistoryStore
  memories: MemoryStore
  /** 请 AI 服务整理记忆，返回要执行的操作 */
  extract(req: ExtractRequest): Promise<MemoryOp[]>
  /** 记忆变了：刷新界面、触发同步 */
  onChanged(): void
}

/** 她说完话后等一会儿再整理：你可能马上接着说，攒在一起整理一次 */
const DELAY_MS = 1500
/** 带上最近三轮对话：只看最后一句容易误会（「对，就是那个」指的是什么） */
const RECENT = 6
/** 上次没整理成（AI 服务没开、大模型超时）：攒下来的一起补，最多这么多句（AI 服务一次最多收 20 句） */
const CATCH_UP = 18
/** 没整理成以后隔多久再试：半分钟、两分钟、十分钟；还不行就等下一轮对话再说 */
export const RETRY_MS = [30_000, 120_000, 600_000]

/** 这次要整理的几句：上次整理到哪句之后的（前面再带两句做上下文），没有记录就是最近三轮 */
export function pendingWindow<T extends { id: string }>(visible: T[], lastDone: string): T[] {
  const at = lastDone ? visible.findIndex((m) => m.id === lastDone) : -1
  if (at < 0) return visible.slice(-RECENT)
  const from = Math.max(0, at + 1 - 2, visible.length - CATCH_UP)
  return visible.slice(from)
}

export function forAi(list: StoredMemory[]): MemoryForAi[] {
  return list.map((m) => ({ id: m.id, content: m.content, kind: m.kind, expiresAt: m.expiresAt ?? null, updatedAt: m.updatedAt }))
}

/**
 * 记忆管家：每轮对话结束后，把最近几轮对话和已有记忆交给 AI 服务，按它给出的操作更新记忆。
 * 同一时间只整理一次；同一轮对话不重复整理。
 * 没整理成就过一会儿再试，补的时候把这段时间攒下的几轮一起交过去（以前失败了就丢了）。
 */
export class MemoryKeeper {
  private timer: ReturnType<typeof setTimeout> | null = null
  private running: Promise<void> | null = null
  private again = false
  private lastDone = ''
  private retryTimer: ReturnType<typeof setTimeout> | null = null
  private failures = 0

  constructor(private deps: KeeperDeps) {}

  /** 她说完了一句（本机的回复） */
  afterReply(): void {
    // 新的一轮来了：重新开始数失败次数（等着的重试也不用了，这次连带着一起整理）
    this.failures = 0
    this.clearRetry()
    if (this.timer) clearTimeout(this.timer)
    this.timer = setTimeout(() => {
      this.timer = null
      void this.run()
    }, DELAY_MS)
  }

  run(): Promise<void> {
    if (this.running) {
      this.again = true
      return this.running
    }
    this.running = (async () => {
      try {
        do {
          this.again = false
          await this.once()
        } while (this.again)
      } finally {
        this.running = null
      }
    })()
    return this.running
  }

  stop(): void {
    if (this.timer) clearTimeout(this.timer)
    this.timer = null
    this.clearRetry()
  }

  private clearRetry(): void {
    if (this.retryTimer) clearTimeout(this.retryTimer)
    this.retryTimer = null
  }

  private scheduleRetry(): void {
    const wait = RETRY_MS[this.failures - 1]
    if (wait === undefined || this.retryTimer) return
    this.retryTimer = setTimeout(() => {
      this.retryTimer = null
      void this.run()
    }, wait)
  }

  private async once(): Promise<void> {
    const recent = pendingWindow(this.deps.history.visible(), this.lastDone)
    const last = recent.at(-1)
    if (!last || last.id === this.lastDone || !recent.some((m) => m.role === 'user')) return
    try {
      const ops = await this.deps.extract({
        messages: recent.map(({ role, content, id, createdAt }) => ({ role, content, id, createdAt })),
        memories: forAi(this.deps.memories.list())
      })
      this.lastDone = last.id
      this.failures = 0
      if (this.deps.memories.apply(ops)) this.deps.onChanged()
    } catch (err) {
      // 整理记忆是后台的事：不打扰用户，过一会儿再试（lastDone 不动，下次连带这几轮一起补）
      this.failures++
      console.warn('[memory] 整理记忆失败：', err instanceof Error ? err.message : err)
      this.scheduleRetry()
    }
  }
}
