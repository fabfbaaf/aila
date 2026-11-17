// 编程 Agent（工作窗口）：主进程、预加载和渲染层共用的类型。事件的含义见 aila-ai 的 /agent 接口

export type AgentMode = 'read_only' | 'ask' | 'auto'

export const MODE_LABELS: Record<AgentMode, string> = {
  read_only: '只读',
  ask: '先问再改',
  auto: '工作区内自动'
}

export const MODE_HINTS: Record<AgentMode, string> = {
  read_only: '只看不改：不改文件，只跑查看类的命令',
  ask: '改文件、跑命令之前先问你（默认）',
  auto: '项目里改文件、跑命令都不问；危险命令照样要问'
}

export interface AgentUsage {
  prompt: number
  completion: number
  cache_hit: number
  cache_miss: number
}

export interface AgentSessionSummary {
  id: string
  cwd: string
  title: string
  mode: AgentMode
  model: string
  created?: string
  updated?: number
  events: number
  usage: AgentUsage
  /** null 或旧服务缺失字段表示当前无法完整计价。 */
  cost?: number | null
  budget?: number | null
  /** 长任务的计划做到哪了（没有计划是 null）：列表里显示「第 2/4 阶段」 */
  plan?: { decision: 'proposed' | 'approved' | 'rejected'; stages: number; done: number; finished: boolean } | null
  running?: boolean
  shell?: string
  /** 在新分支上做时的分支名（放弃了或者不在分支上是 null） */
  branch?: string | null
}

/** 一个事件。带 seq 的写在会话日志里；text_delta、reasoning_delta、tool_start 只是实时推送 */
export interface AgentEvent {
  type: string
  seq?: number
  t?: number
  [key: string]: unknown
}

/** 撤销只追加这一条结果，turn_seq 指向这一轮的 user 事件。 */
export interface AgentUndoEvent extends AgentEvent {
  type: 'undo'
  seq: number
  t: number
  turn_seq: number
  restored: string[]
  skipped: Record<string, string>
  commands: boolean
}

export interface AgentBudgetEvent extends AgentEvent {
  type: 'budget'
  amount: number | null
}

export interface AgentSession extends Omit<AgentSessionSummary, 'events'> {
  events: AgentEvent[]
}

/** 项目记忆的一条（GET /agent/memory?cwd=）：下次在这个项目开会话时放进开头 */
export interface AgentMemoryNote {
  id: string
  text: string
  t: number
  /** agent：她记的；user：你加的 */
  source: 'agent' | 'user'
  session?: string
}

/** 上下文账本（GET /agent/sessions/{id}/context）：下一步发给模型的上下文由哪几块组成 */
export interface AgentLedger {
  /** 模型的上下文窗口（token），快满（compact_at）时压缩 */
  window: number
  compact_at: number
  /** 下一步输入多少 token（按最近一步的真实用量校准；estimated 时只是按字数估的） */
  prompt: number
  /** 最近一步命中缓存的输入 */
  cache_hit: number
  estimated: boolean
  /** 按发送顺序；fixed：每一步都一样的开头（命中缓存的部分） */
  segments: { key: string; label: string; tokens: number; fixed: boolean }[]
  biggest: { label: string; tokens: number }[]
  messages: number
}

/** 她在哪个文件上干活（主进程转给星图：读的星球闪一下，改过的变绿） */
export interface AgentActivity {
  sid: string
  /** 会话的项目目录 */
  cwd: string
  /** 相对项目目录的路径 */
  file: string
  kind: 'read' | 'edit'
}

/** 工作窗口里的任务进展，主进程转给桌面上的她（她说一句、做个动作） */
export type AgentProgress =
  | { kind: 'start'; title: string }
  | { kind: 'confirm'; action: string }
  | { kind: 'done'; files: number }
  | { kind: 'stopped'; reason: string }
  | { kind: 'budget' }
  /** 长任务：一个阶段做完了、整个计划做完了、计划停下了（text 是原因） */
  | { kind: 'stage'; title: string }
  | { kind: 'plan_end' }
  | { kind: 'plan_pause'; text: string }
  | { kind: 'error'; message: string }
/** 浏览器生成的截屏只用文件名在事件里引用，不携带本机路径。 */
export function isAgentScreenshotName(value: unknown): value is string {
  return typeof value === 'string' && value.length === 36 && /^[0-9a-f]{32}\.png$/.test(value)
}
