// 编程 Agent（工作窗口）：主进程、预加载和渲染层共用的类型。事件的含义见 aila-ai 的 /agent 接口

export type AgentMode = 'read_only' | 'ask' | 'auto' | 'full'
export type AgentKind = 'project' | 'computer'

/** 电脑任务没有工作区内自动和全部放开；旧会话没有 kind 时仍按项目处理。 */
export function agentModes(kind: AgentKind = 'project'): AgentMode[] {
  return kind === 'computer' ? ['read_only', 'ask'] : ['read_only', 'ask', 'auto', 'full']
}

/** 开会话前供用户预览的环境说明；实际会话会把同样的说明固定在开头。 */
export interface ComputerContext {
  context: string
  inventory: boolean
}

export const MODE_LABELS: Record<AgentMode, string> = {
  read_only: '只读',
  ask: '先问再改',
  auto: '工作区内自动',
  full: '全部放开'
}

export const MODE_HINTS: Record<AgentMode, string> = {
  read_only: '只看不改：不改文件，只跑查看类的命令',
  ask: '改文件、跑命令之前先问你（默认）',
  auto: '项目里改文件、跑命令都不问；危险命令照样要问',
  full: '什么都不问：改文件、跑命令、开本机网页、在网页上点、用外部工具都直接做；只有危险命令（删除、推送、发布、改系统设置）和密钥文件还要问'
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
  kind?: AgentKind
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
  /** 在等你什么（「等你」收件箱）：confirm 有个操作等你同意；plan 计划等你决定；paused 计划停下了 */
  waiting?: 'confirm' | 'plan' | 'paused' | null
  running?: boolean
  shell?: string
  /** 在新分支上做时的分支名（放弃了或者不在分支上是 null） */
  branch?: string | null
  /** 看板用：她最近一次要用的工具和对象 */
  activity?: { tool: string; target: string } | null
  /** 上一轮怎么结束的（done、error、max_steps、interrupted、budget……）；还没做过是 null */
  outcome?: string | null
  /** 这个会话里改过几个文件 */
  edits?: number
  /** 还没回答的确认（action 是代码按参数生成的说明） */
  pending?: { id: string; tool: string; action: string; can_remember: boolean } | null
  /** 本机的 Codex / Claude Code 在做（不是艾拉自己）：codex、claude；艾拉自己做时没有 */
  engine?: AgentEngine | null
  /** 跨 Agent 派工：派出这个任务的主任务（主 Agent 用艾拉的派工工具派出来的）；自己开的没有 */
  parent?: string | null
}

/** 本机的编程 Agent（通过 ACP 适配器接进来，见 aila-ai/app/runtime/localagents.py） */
export type AgentEngine = 'codex' | 'claude' | 'antigravity' | 'codebuddy'

/** 本机 Agent 能换的一个设置（模型、思考深度、快速模式）：权限模式由艾拉管，不在里面 */
export interface EngineOption {
  id: string
  name: string
  category: 'model' | 'thought_level' | 'speed' | string
  /** 现在用的（你选过的盖在上面，还没连上时也显示你选的） */
  current: string
  choices: { value: string; name: string; description?: string }[]
}

/** 一个本机 Agent 现在的情况：找到没有、登录没有、适配器装了没有 */
export interface LocalAgentInfo {
  id: AgentEngine
  title: string
  path: string | null
  version: string
  /** null：问不出来 */
  logged_in: boolean | null
  adapter: boolean
  /** 自己就会说 ACP，不用适配器（Antigravity、CodeBuddy） */
  native?: boolean
  ready: boolean
  /** 不能用的原因（给你看的） */
  problem: string
  /** 怎么弄好（登录命令、装什么） */
  hints: string[]
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

/** 自定义命令（GET /agent/commands）：项目 .aila/commands/ 或自己的 commands/ 里的「/名字」 */
export interface AgentCommand {
  name: string
  description: string
  /** project：项目里的；user：自己的（每个项目都能用） */
  source: 'project' | 'user'
  path: string
}

export interface AgentCommandList {
  commands: AgentCommand[]
  /** 项目命令放哪（电脑任务没有） */
  project_dir: string | null
  user_dir: string
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
  | { kind: 'confirm'; action: string; sid?: string }
  | { kind: 'done'; files: number }
  | { kind: 'stopped'; reason: string }
  | { kind: 'budget' }
  /** 长任务：一个阶段做完了、整个计划做完了、计划停下了（text 是原因） */
  | { kind: 'stage'; title: string }
  | { kind: 'plan_end' }
  | { kind: 'plan_pause'; text: string }
  | { kind: 'error'; message: string }
  /** 她跑了一次测试：ok 过没过；streak 连着没过几次（过了是 0）；recovered 之前没过、这次过了 */
  | { kind: 'tests'; ok: boolean; streak: number; recovered: boolean }

/** 是不是在跑测试（她用 run_command 跑的那条命令）：常见的测试命令 */
export function isTestCommand(command: string): boolean {
  return /(^|[\s;&|/\\])(pytest|vitest|jest|mocha)(\s|$)|-m\s+pytest|\b(npm|pnpm|yarn)(\.cmd)?\s+(run\s+)?test\b|\bnpx(\.cmd)?\s+(vitest|jest)\b|mvnw?(\.cmd)?\s+(-\S+\s+)*test\b|\bgradlew?\s+test\b|\b(go|cargo|dotnet)\s+test\b|\bnode\s+--test\b/i.test(
    command
  )
}
/** 浏览器生成的截屏只用文件名在事件里引用，不携带本机路径。 */
export function isAgentScreenshotName(value: unknown): value is string {
  return typeof value === 'string' && value.length === 36 && /^[0-9a-f]{32}\.png$/.test(value)
}
