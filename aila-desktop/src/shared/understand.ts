/**
 * 「看懂项目」的数据（AI 服务 aila-ai/app/code/understand.py、explain.py）：
 * 整体架构 → 模块 → 文件接口链路 → 函数，图由代码算；讲解是 AI 说的，和事实分开放。
 */

export type LinkKindText = 'call' | 'import' | 'http' | 'ipc'

/** 一层里的一个方块：子目录或文件 */
export interface AreaChild {
  id: string
  name: string
  kind: 'dir' | 'file'
  files: number
  lines: number
  functions: number
  tests: number
  onlyTests: boolean
  langs: Record<string, number>
}

/** 这一层外面、和里面有连线的一块（兄弟目录或者另一个顶层目录） */
export interface AreaOutside {
  id: string
  name: string
  kind: 'outside'
}

/** 两块之间汇总后的连线：每种连了几处，经过的接口路径、IPC 频道 */
export interface AreaLink {
  source: string
  target: string
  kinds: Partial<Record<LinkKindText, number>>
  via: string[]
}

export interface AreaView {
  path: string
  name: string
  /** 上一层；整个项目时是 null */
  parent: string | null
  children: AreaChild[]
  outside: AreaOutside[]
  links: AreaLink[]
  /** 从哪里开始读 */
  entries: string[]
  /** 被用得最多的 */
  hubs: string[]
  /** 太多没列出来的 */
  hidden: number
}

export interface ChainNode {
  id: string
  name: string
  group?: string
  lang?: string
  lines?: number
  functions?: number
  /** 离起点几步：往下是正数，往上（用到它的）是负数 */
  layer: number
  start: boolean
}

export interface ChainLink {
  source: string
  target: string
  kind: LinkKindText
  weight: number
  via: string[]
}

export interface ChainView {
  start: string
  nodes: ChainNode[]
  links: ChainLink[]
}

export interface EndpointInfo {
  kind: 'http' | 'ipc'
  name: string
  handlers: string[]
}

export interface FnBrief {
  kind: string
  qname: string
  file: string
  start: number
  end: number
  signature: string
  doc?: string
}

export interface FileDetail {
  file: string
  uses: string[]
  usedBy: string[]
  outline: FnBrief[]
}

export interface FunctionDetail extends FnBrief {
  doc: string
  lines: string[]
  truncated: boolean
  callers: FnBrief[]
  callees: FnBrief[]
}

export type ExplainLevel = 'area' | 'chain' | 'file' | 'function'
export type ExplainDepth = 'beginner' | 'dev'

export interface ExplainTarget {
  level: ExplainLevel
  path?: string
  file?: string
  line?: number
  endpoint?: string
  depth: ExplainDepth
}

/** AI 的讲解（没填密钥、出错时是 null，note 说为什么） */
export interface Explanation {
  facts: string
  explanation: { summary: string; detail: string; watch: string } | null
  cached: boolean
  note: string
}

export interface Diagnosis extends Explanation {
  found: boolean
  frames: { file: string; line: number; qname: string | null }[]
  chain: FnBrief[]
  /** 图上要标出来的文件（出错的在前） */
  files: string[]
}
