// 账号和同步：主进程、预加载和渲染层共用的类型

/** 本地保存的一条对话。seq 是云端分配的序号，没有就是还没上传 */
export interface StoredMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  /** ISO 时间 */
  createdAt: string
  seq?: number
  deleted?: boolean
}

/** 同步状态：signed-out 没登录；connecting 正在连；online 已连上；offline 连不上（会自动重连） */
export type SyncStatus = 'signed-out' | 'connecting' | 'online' | 'offline'

export interface DeviceInfo {
  deviceId: string
  deviceName: string
  lastActiveAt: string
}

export interface AccountStatus {
  serverUrl: string
  status: SyncStatus
  email?: string
  displayName?: string
  /** 这台电脑还有多少条没传上去 */
  pending: number
  devices?: DeviceInfo[]
  currentDeviceId?: string
  /** 聊天记录或记忆存不进文件时的错误（存上了就没有）：重启会丢，运行状态页上提醒 */
  saveError?: string
}

export interface AuthForm {
  mode: 'login' | 'register'
  email: string
  password: string
  displayName?: string
  serverUrl: string
}

/** 登录、注册的结果；失败时 error 是可以直接给用户看的话 */
export interface AuthResult {
  ok: boolean
  error?: string
}

/** 云端同步过来的变化 */
export interface RemoteChanges {
  added: StoredMessage[]
  removed: string[]
  settings: { key: string; value: unknown; deleted: boolean }[]
  /** 记忆有变化（列表另外通过 memory:changed 发给界面） */
  memories?: boolean
}

export type MemoryKind = 'profile' | 'preference' | 'event' | 'other'

/** 她记得的一件事。dirty：本地改过、还没传上去 */
export interface StoredMemory {
  id: string
  content: string
  kind: MemoryKind
  /** 有时效的事（「下周三面试」）过了这个时间就不再用 */
  expiresAt?: string | null
  createdAt: string
  updatedAt: string
  seq?: number
  deleted?: boolean
  dirty?: boolean
}

/** AI 服务整理出来的记忆操作 */
export type MemoryOp =
  | { op: 'add'; content: string; kind: MemoryKind; expiresAt: string | null }
  | { op: 'update'; id: string; content: string; kind: MemoryKind; expiresAt: string | null }
  | { op: 'delete'; id: string }

export const DEFAULT_SERVER_URL = 'http://127.0.0.1:8790'

/** 她记下的经历（本机 AI 服务的记忆库里，不同步）：滑出对话窗口的那段的摘要、编程任务每一轮做了什么 */
export interface MemoryEpisode {
  id: string
  content: string
  /** chat：对话摘要；task：编程任务 */
  source: string
  /** 编程任务在哪个项目（对话摘要是空的） */
  project: string
  at: string
}

// ---------- 资料库和技能包（本地 AI 服务提供） ----------

export interface KnowledgeDoc {
  id: string
  name: string
  size: number
  chars: number
  chunks: number
  addedAt: string
}

export interface KnowledgeAddResult {
  added: KnowledgeDoc[]
  errors: string[]
}

export interface SkillInfo {
  id: string
  name: string
  description: string
  enabled: boolean
  available: boolean
  /** read：直接用；write：每次都要用户确认；mixed：看情况（比如点普通链接不用，点提交按钮要） */
  tools?: { name: string; risk: 'read' | 'write' | 'mixed' | string }[]
}

/** 用户对技能包的开关（覆盖默认值），跟着账号同步 */
export type SkillOverrides = Record<string, boolean>
export const SKILLS_SETTING = 'skills'

const MEMORY_KINDS: readonly MemoryKind[] = ['profile', 'preference', 'event', 'other']
/** 和 AI 服务整理记忆时的上限差不多（app/memory/extract.py 的 MAX_CONTENT） */
export const MEMORY_MAX = 120

/** 记忆页里你自己加、改的一条：检查好变成操作，不对就返回要告诉你的话 */
export function cleanMemoryEdit(raw: unknown): MemoryOp | string {
  if (!raw || typeof raw !== 'object') return '内容不对。'
  const r = raw as Record<string, unknown>
  const content = typeof r.content === 'string' ? r.content.replace(/\s+/g, ' ').trim() : ''
  if (!content) return '写点什么再保存。'
  if (content.length > MEMORY_MAX) return `一条最多 ${MEMORY_MAX} 个字，挑最要紧的写。`
  const kind = MEMORY_KINDS.includes(r.kind as MemoryKind) ? (r.kind as MemoryKind) : 'other'
  let expiresAt: string | null = null
  if (typeof r.expiresAt === 'string' && r.expiresAt) {
    const d = new Date(r.expiresAt)
    if (Number.isNaN(d.getTime())) return '日期不对。'
    expiresAt = d.toISOString()
  }
  if (r.op === 'add') return { op: 'add', content, kind, expiresAt }
  if (r.op === 'update' && typeof r.id === 'string' && r.id && r.id.length <= 64) return { op: 'update', id: r.id, content, kind, expiresAt }
  return '内容不对。'
}
