// 魔法工作台（代码星图）：主进程、预加载和渲染层共用的类型

/** 一颗星球 = 一个文件 */
export interface GraphNode {
  id: string // 相对项目根目录的路径
  name: string
  group: string // 子项目或顶层目录：同一组颜色一样、聚在一起
  lang: string
  lines: number
  functions: number
  modified: boolean // 还没提交的改动
}

/** 一条光线 = 一个文件调用另一个文件（同一对文件之间的调用汇总，weight 是次数） */
export interface GraphLink {
  source: string
  target: string
  weight: number
}

export interface CodeGraph {
  nodes: GraphNode[]
  links: GraphLink[]
}

/** 文件里的函数和类（全息面板列出来） */
export interface OutlineItem {
  kind: string
  qname: string
  start: number
  end: number
  signature: string
  doc: string
}

/** 一段代码（磁盘上现在的样子） */
export interface ChunkSource {
  kind: string
  qname: string
  file: string
  start: number
  end: number
  lines: string[]
  truncated: boolean
}

/** 报错落在哪个项目、哪些函数上，以及往上的调用链 */
export interface LocatedError {
  project: string
  name: string
  frames: { file: string | null; line: number; qname: string | null }[]
  chain: { file: string; qname: string; line: number }[]
}

/** 打开星图时要做的事：看哪个项目、标出哪个报错 */
export interface WorkbenchFocus {
  project?: string
  error?: string
}

/** 她准备好、正等用户点头的代码修改（确认里的 detail：项目、说明、测试、diff） */
export interface PendingFix {
  id: string
  detail: string
  /** chat：对话里她提的修改（角色窗口的气泡里也能点「好」）；agent：工作窗口里的改文件操作 */
  source?: 'chat' | 'agent'
}

/** 修代码的确认：星图里双手合十只能确认这一种，别的操作（打开网页、提交表单）还是要在她那里点 */
export const FIX_SKILL = '代码库'
export const FIX_ACTION = '按这个改动修代码'

export function pendingFixOf(ask: { id: string; skill: string; action: string; detail: string } | null): PendingFix | null {
  return ask && ask.skill === FIX_SKILL && ask.action === FIX_ACTION ? { id: ask.id, detail: ask.detail } : null
}
