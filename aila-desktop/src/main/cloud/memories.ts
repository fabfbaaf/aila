import { randomUUID } from 'crypto'
import { mkdirSync, readFileSync, renameSync, writeFileSync } from 'fs'
import { dirname } from 'path'
import type { MemoryOp, StoredMemory } from '../../shared/cloud'

const SAVE_DELAY_MS = 300

/**
 * 她记得的事，本地存一份（没登录也能用）。和对话记录一样也是同步的发件箱：dirty 的就是还没传上去的。
 *
 * 冲突规则：
 * - 删除优先：云端说删了，本地哪怕有没传上去的修改也一起删（用户在别的电脑上让她忘掉的事，不能被复活）
 * - 否则本地没传上去的修改优先（马上会传上去，成为最新版本）
 * - 都没有就以序号大的为准
 */
export class MemoryStore {
  private items = new Map<string, StoredMemory>()
  private saveTimer: ReturnType<typeof setTimeout> | null = null

  constructor(private file: string | null) {}

  load(): void {
    if (!this.file) return
    try {
      const data = JSON.parse(readFileSync(this.file, 'utf-8')) as StoredMemory[]
      this.items = new Map((Array.isArray(data) ? data : []).map((m) => [m.id, m]))
    } catch {
      this.items = new Map()
    }
  }

  /** 给界面和 AI 服务用：没删的，最近更新的在前 */
  list(): StoredMemory[] {
    return [...this.items.values()].filter((m) => !m.deleted).sort((a, b) => (a.updatedAt < b.updatedAt ? 1 : -1))
  }

  dirty(): StoredMemory[] {
    return [...this.items.values()].filter((m) => m.dirty)
  }

  /** 执行 AI 服务整理出来的操作。返回有没有变化 */
  apply(ops: MemoryOp[], now = new Date()): boolean {
    const at = now.toISOString()
    let changed = false
    for (const op of ops) {
      if (op.op === 'add') {
        const id = randomUUID()
        this.items.set(id, { id, content: op.content, kind: op.kind, expiresAt: op.expiresAt, createdAt: at, updatedAt: at, dirty: true })
        changed = true
      } else {
        const m = this.items.get(op.id)
        if (!m || m.deleted) continue
        if (op.op === 'delete') this.remove(m, at)
        else Object.assign(m, { content: op.content, kind: op.kind, expiresAt: op.expiresAt, updatedAt: at, dirty: true })
        changed = true
      }
    }
    if (changed) this.scheduleSave()
    return changed
  }

  /** 用户在界面上删掉一条 */
  delete(id: string): boolean {
    const m = this.items.get(id)
    if (!m || m.deleted) return false
    this.remove(m, new Date().toISOString())
    this.scheduleSave()
    return true
  }

  /** 全部忘掉 */
  deleteAll(): number {
    const at = new Date().toISOString()
    const alive = [...this.items.values()].filter((m) => !m.deleted)
    for (const m of alive) this.remove(m, at)
    if (alive.length) this.scheduleSave()
    return alive.length
  }

  markSynced(id: string, seq: number): void {
    const m = this.items.get(id)
    if (!m) return
    m.dirty = false
    m.seq = seq
    if (m.deleted) this.items.delete(id) // 删除已经传上去了，本地不用再留墓碑
    this.scheduleSave()
  }

  /** 云端说这条早就删了（410）：放弃本地的修改，也删掉 */
  markGone(id: string): void {
    this.items.delete(id)
    this.scheduleSave()
  }

  /** 云端的变化。返回界面要不要刷新 */
  applyRemote(remote: StoredMemory & { seq: number }): boolean {
    const local = this.items.get(remote.id)
    if (remote.deleted) {
      if (!local || local.deleted) {
        if (local) this.items.delete(remote.id)
        return false
      }
      this.items.delete(remote.id) // 删除优先
      this.scheduleSave()
      return true
    }
    if (local?.dirty) return false // 本地的修改还没传上去，传上去之后它就是最新的
    if (local && (local.seq ?? 0) >= remote.seq) return false
    this.items.set(remote.id, { ...remote, dirty: false })
    this.scheduleSave()
    return true
  }

  clear(): void {
    this.items = new Map()
    this.saveNow()
  }

  saveNow(): void {
    if (this.saveTimer) clearTimeout(this.saveTimer)
    this.saveTimer = null
    if (!this.file) return
    mkdirSync(dirname(this.file), { recursive: true })
    const tmp = this.file + '.tmp'
    writeFileSync(tmp, JSON.stringify([...this.items.values()]), 'utf-8')
    renameSync(tmp, this.file)
  }

  private remove(m: StoredMemory, at: string): void {
    if (m.seq === undefined) {
      this.items.delete(m.id) // 从没传上去过，直接删，不用通知云端
      return
    }
    Object.assign(m, { deleted: true, content: '', updatedAt: at, dirty: true })
  }

  private scheduleSave(): void {
    if (!this.file || this.saveTimer) return
    this.saveTimer = setTimeout(() => this.saveNow(), SAVE_DELAY_MS)
  }
}
