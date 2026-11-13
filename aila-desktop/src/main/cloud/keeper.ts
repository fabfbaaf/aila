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
  messages: { role: 'user' | 'assistant'; content: string }[]
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

export function forAi(list: StoredMemory[]): MemoryForAi[] {
  return list.map((m) => ({ id: m.id, content: m.content, kind: m.kind, expiresAt: m.expiresAt ?? null, updatedAt: m.updatedAt }))
}

/**
 * 记忆管家：每轮对话结束后，把最近几轮对话和已有记忆交给 AI 服务，按它给出的操作更新记忆。
 * 同一时间只整理一次；同一轮对话不重复整理。
 */
export class MemoryKeeper {
  private timer: ReturnType<typeof setTimeout> | null = null
  private running: Promise<void> | null = null
  private again = false
  private lastDone = ''

  constructor(private deps: KeeperDeps) {}

  /** 她说完了一句（本机的回复） */
  afterReply(): void {
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
  }

  private async once(): Promise<void> {
    const recent = this.deps.history.visible().slice(-RECENT)
    const last = recent.at(-1)
    if (!last || last.id === this.lastDone || !recent.some((m) => m.role === 'user')) return
    try {
      const ops = await this.deps.extract({
        messages: recent.map(({ role, content }) => ({ role, content })),
        memories: forAi(this.deps.memories.list())
      })
      this.lastDone = last.id
      if (this.deps.memories.apply(ops)) this.deps.onChanged()
    } catch (err) {
      // 整理记忆是后台的事：AI 服务没开或出错了，这轮就不记，不打扰用户
      console.warn('[memory] 整理记忆失败：', err instanceof Error ? err.message : err)
    }
  }
}
