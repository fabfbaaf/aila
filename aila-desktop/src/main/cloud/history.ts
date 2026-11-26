import { readFileSync } from 'fs'
import type { StoredMessage } from '../../shared/cloud'
import { Saver } from './persist'

/** 本地最多留这么多条（云端是全量的） */
const MAX_LOCAL = 2000
const SAVE_DELAY_MS = 300

/**
 * 本地对话记录，存成 JSON 文件。没登录时也用：重启后她还记得刚才聊到哪了。
 * 登录后它同时是同步的「发件箱」：没有 seq 的消息就是还没传上去的。
 */
export class HistoryStore {
  private messages: StoredMessage[] = []
  private byId = new Map<string, StoredMessage>()
  private saver: Saver

  constructor(private file: string | null) {
    this.saver = new Saver(file, () => JSON.stringify(this.messages), SAVE_DELAY_MS)
  }

  load(): void {
    if (!this.file) return
    try {
      const data = JSON.parse(readFileSync(this.file, 'utf-8')) as StoredMessage[]
      this.reset(Array.isArray(data) ? data : [])
    } catch {
      this.reset([]) // 第一次运行或文件坏了
    }
  }

  /** 给对话用的：没删的，按时间排好 */
  visible(): StoredMessage[] {
    return this.messages.filter((m) => !m.deleted)
  }

  /** 还没上传的 */
  unsynced(): StoredMessage[] {
    return this.messages.filter((m) => m.seq === undefined)
  }

  get size(): number {
    return this.messages.length
  }

  /** 本机新说的一句。已经有这个 id 时什么都不做 */
  add(m: StoredMessage): boolean {
    if (this.byId.has(m.id)) return false
    const copy = { ...m }
    delete copy.seq
    this.insert(copy)
    this.scheduleSave()
    return true
  }

  markSynced(id: string, seq: number): void {
    const m = this.byId.get(id)
    if (m && m.seq === undefined) {
      m.seq = seq
      this.scheduleSave()
    }
  }

  /** 云端传来的变化。返回对界面有影响的结果：新出现的消息，或被删掉的消息 */
  applyRemote(m: StoredMessage & { seq: number }): 'added' | 'removed' | 'unchanged' {
    const existing = this.byId.get(m.id)
    if (!existing) {
      if (m.deleted) return 'unchanged' // 别处说了又删了，这边从没见过，不用管
      this.insert({ ...m })
      this.scheduleSave()
      return 'added'
    }
    if (existing.seq !== undefined && existing.seq >= m.seq) return 'unchanged'
    const wasVisible = !existing.deleted
    existing.seq = m.seq
    if (m.deleted) {
      existing.deleted = true
      existing.content = ''
    }
    this.scheduleSave()
    return wasVisible && m.deleted ? 'removed' : 'unchanged'
  }

  clear(): void {
    this.reset([])
    this.saveNow()
  }

  /** 立刻写盘（退出前调用）。写不进去时记下错误、过一会儿再试（persist.ts） */
  saveNow(): boolean {
    return this.saver.now()
  }

  private insert(m: StoredMessage): void {
    this.byId.set(m.id, m)
    // 大多数时候是按时间追加的，从后往前找插入位置
    let i = this.messages.length
    while (i > 0 && compare(this.messages[i - 1], m) > 0) i--
    this.messages.splice(i, 0, m)
    if (this.messages.length > MAX_LOCAL) {
      // 太多时丢掉最老的、已经同步过的
      const drop = this.messages.findIndex((x) => x.seq !== undefined)
      if (drop >= 0) this.byId.delete(this.messages.splice(drop, 1)[0].id)
    }
  }

  private reset(list: StoredMessage[]): void {
    this.messages = [...list].sort(compare)
    this.byId = new Map(this.messages.map((m) => [m.id, m]))
  }

  private scheduleSave(): void {
    this.saver.schedule()
  }
}

function compare(a: StoredMessage, b: StoredMessage): number {
  if (a.createdAt !== b.createdAt) return a.createdAt < b.createdAt ? -1 : 1
  return (a.seq ?? Infinity) - (b.seq ?? Infinity)
}
