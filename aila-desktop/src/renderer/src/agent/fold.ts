/**
 * 把会话事件折叠成工作窗口里一条条显示的内容（纯逻辑，可以单测）。
 *
 * - 她说的话：逐字事件拼成一段，一步结束时用完整的 assistant 文字校正
 * - 工具：第一次出现（要确认、开始执行、出结果，哪个先到算哪个）时加一张卡片，之后原地更新；
 *   要确认的操作，确认按钮就在这张卡片里
 * - 一轮结束：一行统计（几步、多久、输入多少、命中缓存多少、输出多少）
 * - 整个会话：最新的待办清单、改过的文件、累计用量、权限模式、是不是正在做
 */
import type { AgentEvent, AgentMode, AgentUndoEvent } from '../../../shared/agent'
import { isAgentScreenshotName } from '../../../shared/agent'
import { validBudget, yuan } from '../../../shared/budget'
import { t, localized } from '../i18n'

export interface TodoItem {
  content: string
  status: 'pending' | 'in_progress' | 'completed'
}

export interface ConfirmState {
  id: string
  action: string
  detail: string
  reason: string
  kind: string
  canRemember: boolean
  result?: string
  remember?: boolean
}

export interface ToolItem {
  kind: 'tool'
  key: string
  name: string
  args: Record<string, unknown>
  action: string
  status: string // pending / asking / running / ok / error / denied / rejected / timeout / skipped / interrupted
  content: string
  diff?: string
  file?: string
  todos?: TodoItem[]
  exitCode?: number | null
  screenshot?: string
  confirm?: ConfirmState
  /** explore、delegate：派出去的助手用的模型、走了几步（报告在 content 里） */
  sub?: { model: string; steps: number }
  /** 跑得久的工具（子 Agent）这会儿在做什么：谁、在做什么。做完就清掉 */
  progress?: { who: string; text: string }
  /** delegate：子 Agent 在自己的分支上改了哪些文件（「M 路径」） */
  files?: string[]
}

export interface EndItem {
  kind: 'end'
  key: string
  reason: string
  error?: string
  steps: number
  seconds: number
  prompt: number
  hit: number
  completion: number
  estimated: boolean
  cost?: number | null
  undo?: UndoPreview
}

export interface UndoPreview {
  turnSeq: number
  files: { path: string; created: boolean }[]
  missing: string[]
  commands: boolean
}

export interface MergeItem {
  kind: 'merge'
  key: string
  merged: boolean
  reason: string
  files: string[]
  tests: { label: string; ok: boolean; seconds: number; tail: string }[]
  conflicts: string[]
  /** 没找到测试命令：可以选「不跑测试直接合并」 */
  noTests: boolean
}

export type Item =
  /**
   * 代码发的那几种（/review、出方案、计划的某个阶段）：text 是一句话的标题，完整的请求不摆出来。
   * 自定义命令（tag: command）：text 是「/发布 v1.2」，detail 是按命令文件展开后真正发给她的话（可以展开看）
   */
  | { kind: 'user'; key: string; text: string; review?: boolean; tag?: 'review' | 'plan' | 'stage' | 'command'; detail?: string }
  | MergeItem
  | { kind: 'text'; key: string; text: string; live: boolean }
  | ToolItem
  | EndItem
  | { kind: 'undo'; key: string; result: AgentUndoEvent }
  | { kind: 'note'; key: string; text: string }

export const MODE_NOTE: Record<string, string> = localized({ read_only: '只读', ask: '先问再改', auto: '工作区内自动' })

export function actionOf(name: string, args: Record<string, unknown>): string {
  const s = (k: string): string => String(args[k] ?? '')
  switch (name) {
    case 'read_file':
      return t('读 {path}', { path: s('path') })
    case 'list_files':
      return t('找文件 {pattern}', { pattern: s('pattern') || '*' })
    case 'search':
      return t('搜「{q}」', { q: s('pattern').slice(0, 40) })
    case 'edit_file':
      return t('改 {path}', { path: s('path') })
    case 'write_file':
      return t('写 {path}', { path: s('path') })
    case 'run_command':
      return (args.background ? t('后台：') : '') + (s('description') || s('command')).slice(0, 80)
    case 'todo_write':
      return t('更新待办清单')
    case 'code_search':
      return t('按意思搜「{q}」', { q: s('query').slice(0, 24) })
    case 'code_locate':
      return t('按报错定位代码')
    case 'code_symbol':
      return t('看 {name} 的调用关系', { name: s('name').slice(0, 30) })
    case 'explore':
      return t('派助手去查「{task}」', { task: s('task').trim().slice(0, 30) })
    case 'delegate':
      return args.role === 'review'
        ? t('派审查员「{title}」', { title: s('title').trim().slice(0, 30) })
        : t('派助手做「{title}」', { title: s('title').trim().slice(0, 30) })
    case 'merge_work':
      return t('收回助手 {id} 的改动', { id: s('id') })
    case 'discard_work':
      return t('不要助手 {id} 的改动', { id: s('id') })
    case 'web_search':
      return t('搜网页「{q}」', { q: s('query').slice(0, 40) })
    case 'web_fetch':
      return t('读网页 {url}', { url: s('url').replace(/^https?:\/\//, '').slice(0, 60) })
    case 'job_output':
      return t('看后台任务 {id} 的输出', { id: s('id') })
    case 'job_stop':
      return t('停掉后台任务 {id}', { id: s('id') })
    case 'propose_plan':
      return t('交计划给你看')
    case 'stage_done':
      return t('这个阶段做完了')
    case 'stage_blocked':
      return t('这个阶段卡住了')
    case 'remember':
      return t('记进项目记忆')
    default:
      return name
  }
}

function parseArgs(raw: unknown): Record<string, unknown> {
  try {
    const v = JSON.parse(String(raw || '{}'))
    return v && typeof v === 'object' && !Array.isArray(v) ? v : {}
  } catch {
    return {}
  }
}

export function readUndo(e: AgentEvent): AgentUndoEvent | undefined {
  if (e.type !== 'undo' || typeof e.seq !== 'number' || typeof e.turn_seq !== 'number' ||
      !Array.isArray(e.restored) || !e.skipped || typeof e.skipped !== 'object' || Array.isArray(e.skipped)) return
  return {
    type: 'undo',
    seq: e.seq,
    t: e.t ?? 0,
    turn_seq: e.turn_seq,
    restored: e.restored.filter((path): path is string => typeof path === 'string'),
    skipped: Object.fromEntries(Object.entries(e.skipped).filter(([, reason]) => typeof reason === 'string')),
    commands: e.commands === true
  }
}

function newTurn(t = 0, seq?: number) {
  return {
    seq, start: t, steps: 0, prompt: 0, hit: 0, completion: 0, estimated: false,
    edits: new Map<string, { created: boolean; backedUp: boolean }>(), commands: false
  }
}

export class Transcript {
  items: Item[] = []
  lastSeq = -1
  running = false
  mode: AgentMode = 'ask'
  model = ''
  budget: number | null = null
  todos: TodoItem[] = []
  /** 改过的文件 → 改了几次 */
  files = new Map<string, number>()
  usage = { prompt: 0, hit: 0, completion: 0, estimated: false }
  /** 在新分支上做：分支名（放弃了是 null）；closed：分支放弃了，这个会话不再接任务 */
  branch: string | null = null
  closed = false
  /** 写在日志里的事件（执行轨迹要用）；每一步的输入和命中缓存（上下文账本要用） */
  events: AgentEvent[] = []
  steps: { prompt: number; hit: number }[] = []

  private calls = new Map<string, { name: string; args: Record<string, unknown> }>()
  private tools = new Map<string, ToolItem>()
  private turn = newTurn()
  private lastEnd: EndItem | undefined
  private undone = new Set<number>()
  /** 第几轮：工具调用的 id 只在一轮里唯一（有的模型每轮都从 call_0 开始编号），按「轮 + id」找卡片 */
  private turnNo = 0
  private n = 0

  apply(e: AgentEvent): void {
    if (typeof e.seq === 'number') {
      if (e.seq <= this.lastSeq) return // 重复的（补发和实时重叠）
      this.lastSeq = e.seq
      this.events.push(e)
    }
    switch (e.type) {
      case 'start':
        this.mode = (e.mode as AgentMode) ?? 'ask'
        this.model = String(e.model ?? '')
        this.budget = validBudget(e.budget) ? e.budget : null
        this.branch = (e.branch as { branch?: string } | undefined)?.branch ?? null
        break
      case 'merge':
        this.items.push({
          kind: 'merge',
          key: this.key(),
          merged: Boolean(e.merged),
          reason: String(e.reason ?? ''),
          files: (e.files as string[]) ?? [],
          tests: (e.tests as MergeItem['tests']) ?? [],
          conflicts: (e.conflicts as string[]) ?? [],
          noTests: !e.merged && [ '没找到这个项目的测试命令', t('没找到这个项目的测试命令') ].some((k) => String(e.reason ?? '').includes(k)) // AI 服务的原因可能是中文也可能已经翻好
        })
        break
      case 'branch_closed':
        this.items.push({ kind: 'note', key: this.key(), text: t('放弃了分支 {branch}：你的代码保持原样', { branch: String(e.branch ?? '') }) })
        this.branch = null
        this.closed = true
        break
      case 'user':
        this.closeText()
        if (e.review) this.items.push({ kind: 'user', key: this.key(), text: String(e.title ?? t('审查没提交的改动')), review: true, tag: 'review' })
        else if (e.plan_request) this.items.push({ kind: 'user', key: this.key(), text: String(e.title ?? t('出方案')), tag: 'plan' })
        else if (typeof e.plan_stage === 'number') this.items.push({ kind: 'user', key: this.key(), text: String(e.title ?? t('第 {n} 阶段', { n: e.plan_stage + 1 })), tag: 'stage' })
        else if (typeof e.command === 'string')
          this.items.push({ kind: 'user', key: this.key(), text: String(e.title ?? `/${e.command}`), tag: 'command', detail: String(e.text ?? '') })
        else this.items.push({ kind: 'user', key: this.key(), text: String(e.text ?? '') })
        this.turn = newTurn(Number(e.t ?? 0), e.seq)
        this.turnNo++
        this.running = true
        break
      case 'text_delta': {
        const last = this.items[this.items.length - 1]
        if (last?.kind === 'text' && last.live) last.text += String(e.text ?? '')
        else this.items.push({ kind: 'text', key: this.key(), text: String(e.text ?? ''), live: true })
        break
      }
      case 'assistant': {
        this.turn.steps++
        const text = String(e.text ?? '')
        const last = this.items[this.items.length - 1]
        if (last?.kind === 'text' && last.live) {
          last.text = text
          last.live = false
          if (!text.trim()) this.items.pop()
        } else if (text.trim()) {
          this.items.push({ kind: 'text', key: this.key(), text, live: false })
        }
        for (const c of (e.tool_calls as { id: string; name: string; arguments: string }[]) ?? []) {
          this.calls.set(`${this.turnNo}:${c.id}`, { name: c.name, args: parseArgs(c.arguments) })
        }
        break
      }
      case 'usage': {
        const prompt = Number(e.prompt ?? 0)
        const hit = Number(e.cache_hit ?? 0)
        const completion = Number(e.completion ?? 0)
        const est = Boolean(e.estimated)
        this.turn.prompt += prompt
        this.turn.hit += hit
        this.turn.completion += completion
        this.turn.estimated ||= est
        this.usage.prompt += prompt
        this.usage.hit += hit
        this.usage.completion += completion
        this.usage.estimated ||= est
        // 压缩、子 Agent（purpose = 工具名）的用量算进总数，但不是主 Agent 的一步
        if (!e.purpose) this.steps.push({ prompt, hit })
        break
      }
      case 'confirm': {
        const tool = this.tool(String(e.call_id ?? ''), String(e.tool ?? ''))
        tool.status = 'asking'
        tool.confirm = {
          id: String(e.id),
          action: String(e.action ?? ''),
          detail: String(e.detail ?? ''),
          reason: String(e.reason ?? ''),
          kind: String(e.kind ?? ''),
          canRemember: Boolean(e.can_remember)
        }
        break
      }
      case 'confirm_end': {
        for (const t of this.tools.values()) {
          const c = t.confirm
          if (c && c.id === e.id) {
            c.result = String(e.result ?? '')
            c.remember = Boolean(e.remember)
            if (e.result === 'approved') t.status = 'pending'
          }
        }
        break
      }
      case 'tool_start': {
        const tool = this.tool(String(e.id), String(e.name ?? ''))
        tool.status = 'running'
        if (e.status) tool.action = String(e.status)
        break
      }
      case 'tool_progress': {
        const tool = this.tools.get(`${this.turnNo}:${String(e.id)}`)
        if (tool && tool.status === 'running') tool.progress = { who: String(e.who ?? ''), text: String(e.text ?? '') }
        break
      }
      case 'tool': {
        const tool = this.tool(String(e.id), String(e.name ?? ''))
        tool.status = String(e.status ?? 'ok')
        tool.content = String(e.content ?? '')
        tool.progress = undefined
        if (Array.isArray(e.files) && tool.name === 'delegate') tool.files = e.files.map(String)
        if (tool.name === 'browser_screenshot' && tool.status === 'ok' && isAgentScreenshotName(e.screenshot)) {
          tool.screenshot = e.screenshot
        }
        if (typeof e.diff === 'string') tool.diff = e.diff
        if (typeof e.file === 'string') {
          tool.file = e.file
          if (tool.status === 'ok') this.files.set(e.file, (this.files.get(e.file) ?? 0) + 1)
        }
        if (Array.isArray(e.todos)) {
          tool.todos = e.todos as TodoItem[]
          this.todos = tool.todos
        }
        if ('exit_code' in e) tool.exitCode = e.exit_code as number | null
        if (typeof e.sub === 'string') tool.sub = { model: String(e.model ?? ''), steps: Number(e.steps ?? 0) }
        if (e.status === 'ok' && tool.name === 'run_command') this.turn.commands = true
        if (e.status === 'ok' && (tool.name === 'edit_file' || tool.name === 'write_file') && typeof e.file === 'string') {
          const previous = this.turn.edits.get(e.file)
          const backedUp = typeof e.after === 'string' && e.after.length > 0 &&
            (e.created === true || (typeof e.snap === 'number' && Number.isSafeInteger(e.snap) && e.snap >= 0))
          this.turn.edits.set(e.file, { created: previous?.created ?? (e.created === true), backedUp: backedUp && (previous?.backedUp ?? true) })
        }
        break
      }
      case 'plan_decision':
        this.closeText()
        this.items.push({ kind: 'note', key: this.key(), text: e.approved ? (e.pause_each ? t('你批准了计划：每个阶段做完停一下') : t('你批准了计划：按阶段做下去')) : t('你没要这份计划') })
        break
      case 'plan_pause':
        this.closeText()
        this.items.push({ kind: 'note', key: this.key(), text: t('计划停在第 {n} 阶段：{why}', { n: Number(e.stage ?? 0) + 1, why: String(e.text ?? '') }) })
        break
      case 'plan_end':
        this.closeText()
        this.items.push({ kind: 'note', key: this.key(), text: t('计划全部做完了') })
        break
      case 'compaction':
        this.closeText()
        this.items.push({ kind: 'note', key: this.key(), text: t('上下文快满了：用交接说明代替了前面的对话') })
        break
      case 'mode':
        this.mode = e.mode as AgentMode
        this.items.push({ kind: 'note', key: this.key(), text: t('权限模式：{mode}', { mode: MODE_NOTE[String(e.mode)] ?? String(e.mode) }) })
        break
      case 'budget':
        if (validBudget(e.amount)) {
          this.budget = e.amount
          this.items.push({ kind: 'note', key: this.key(), text: e.amount === null ? t('花费上限已取消') : t('花费上限设为 {money}', { money: yuan(e.amount) }) })
        }
        break
      case 'turn_end': {
        this.closeText()
        const t = this.turn
        const undo = t.seq === undefined ? undefined : {
          turnSeq: t.seq,
          files: [...t.edits].filter(([, edit]) => edit.backedUp).map(([path, edit]) => ({ path, created: edit.created })),
          missing: [...t.edits].filter(([, edit]) => !edit.backedUp).map(([path]) => path),
          commands: t.commands
        }
        const end: EndItem = {
          kind: 'end',
          key: this.key(),
          reason: String(e.reason ?? ''),
          error: e.error ? String(e.error) : undefined,
          steps: t.steps,
          seconds: t.start && e.t ? Math.max(0, Number(e.t) - t.start) : 0,
          prompt: t.prompt,
          hit: t.hit,
          completion: t.completion,
          estimated: t.estimated,
          cost: validBudget(e.cost) ? e.cost : null,
          undo
        }
        this.items.push(end)
        this.lastEnd = end
        // 还在等的卡片（被打断时）不再转圈
        for (const tool of this.tools.values()) if (['pending', 'running', 'asking'].includes(tool.status)) tool.status = 'interrupted'
        this.running = false
        break
      }
      case 'undo': {
        const result = readUndo(e)
        if (result) {
          this.undone.add(result.turn_seq)
          this.items.push({ kind: 'undo', key: this.key(), result })
        }
        break
      }
    }
  }

  /** 只有最近一轮有底片或新文件，且会话停下、没有撤销过时，才可以给用户预览。 */
  get undoTarget(): EndItem | undefined {
    const end = this.lastEnd
    const undo = end?.undo
    if (!this.running && !this.closed && undo?.files.length && !this.undone.has(undo.turnSeq)) return end
    return undefined
  }

  /** 正在等用户回答的确认 */
  get pending(): ToolItem | undefined {
    for (const t of this.tools.values()) if (t.status === 'asking' && t.confirm && !t.confirm.result) return t
    return undefined
  }

  private tool(id: string, name: string): ToolItem {
    const key = `${this.turnNo}:${id}`
    let tool = this.tools.get(key)
    if (!tool) {
      this.closeText()
      const call = this.calls.get(key)
      const args = call?.args ?? {}
      const toolName = call?.name ?? name
      tool = { kind: 'tool', key: `tool-${key}`, name: toolName, args, action: actionOf(toolName, args), status: 'pending', content: '' }
      this.items.push(tool)
      // 留在列表里的是响应式代理：返回它，后面原地改才会刷新界面
      tool = this.items[this.items.length - 1] as ToolItem
      this.tools.set(key, tool)
    }
    return tool
  }

  private closeText(): void {
    const last = this.items[this.items.length - 1]
    if (last?.kind === 'text') last.live = false
  }

  private key(): string {
    return `i${this.n++}`
  }
}

export function compact(n: number): string {
  return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n)
}

export function endText(end: EndItem): string {
  const words: Record<string, string> = {
    done: t('做完了'),
    max_steps: t('步数到上限了，说「继续」接着做'),
    denied: t('她要做的事连着被权限挡下，先停下了：换个权限模式（比如「先问再改」），或者说清楚接下来怎么做'),
    rejected: t('停下来了，等你说接下来怎么做'),
    timeout: t('一直没等到你的回答，先停下了'),
    interrupted: t('被打断了，说「继续」接着做'),
    budget: t('花到上限了，调高或取消上限后说「继续」接着做'),
    error: end.error ? t('出错了：{why}', { why: end.error }) : t('出错了')
  }
  return words[end.reason] ?? end.reason
}

export function endStats(end: EndItem): string {
  const parts = [t('{n} 步', { n: end.steps }), t('{n} 秒', { n: end.seconds.toFixed(1) })]
  if (end.prompt) {
    parts.push(t('输入 {p}（命中缓存 {pct}%{est}）', { p: compact(end.prompt), pct: Math.round((end.hit / end.prompt) * 100), est: end.estimated ? t('，估算') : '' }))
    parts.push(t('输出 {o}', { o: compact(end.completion) }))
  }
  if (typeof end.cost === 'number' && Number.isFinite(end.cost) && end.cost >= 0) parts.push(yuan(end.cost))
  return parts.join(' · ')
}
