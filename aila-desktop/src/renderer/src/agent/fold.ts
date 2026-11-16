/**
 * 把会话事件折叠成工作窗口里一条条显示的内容（纯逻辑，可以单测）。
 *
 * - 她说的话：逐字事件拼成一段，一步结束时用完整的 assistant 文字校正
 * - 工具：第一次出现（要确认、开始执行、出结果，哪个先到算哪个）时加一张卡片，之后原地更新；
 *   要确认的操作，确认按钮就在这张卡片里
 * - 一轮结束：一行统计（几步、多久、输入多少、命中缓存多少、输出多少）
 * - 整个会话：最新的待办清单、改过的文件、累计用量、权限模式、是不是正在做
 */
import type { AgentEvent, AgentMode } from '../../../shared/agent'

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
  confirm?: ConfirmState
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
  | { kind: 'user'; key: string; text: string }
  | MergeItem
  | { kind: 'text'; key: string; text: string; live: boolean }
  | ToolItem
  | EndItem
  | { kind: 'note'; key: string; text: string }

export const MODE_NOTE: Record<string, string> = { read_only: '只读', ask: '先问再改', auto: '工作区内自动' }

export function actionOf(name: string, args: Record<string, unknown>): string {
  const s = (k: string): string => String(args[k] ?? '')
  switch (name) {
    case 'read_file':
      return `读 ${s('path')}`
    case 'list_files':
      return `找文件 ${s('pattern') || '*'}`
    case 'search':
      return `搜「${s('pattern').slice(0, 40)}」`
    case 'edit_file':
      return `改 ${s('path')}`
    case 'write_file':
      return `写 ${s('path')}`
    case 'run_command':
      return (s('description') || s('command')).slice(0, 80)
    case 'todo_write':
      return '更新待办清单'
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

export class Transcript {
  items: Item[] = []
  lastSeq = -1
  running = false
  mode: AgentMode = 'ask'
  model = ''
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
  private turn = { start: 0, steps: 0, prompt: 0, hit: 0, completion: 0, estimated: false }
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
          noTests: !e.merged && String(e.reason ?? '').includes('没找到这个项目的测试命令')
        })
        break
      case 'branch_closed':
        this.items.push({ kind: 'note', key: this.key(), text: `放弃了分支 ${String(e.branch ?? '')}：你的代码保持原样` })
        this.branch = null
        this.closed = true
        break
      case 'user':
        this.closeText()
        this.items.push({ kind: 'user', key: this.key(), text: String(e.text ?? '') })
        this.turn = { start: Number(e.t ?? 0), steps: 0, prompt: 0, hit: 0, completion: 0, estimated: false }
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
        if (e.purpose !== 'compaction') this.steps.push({ prompt, hit })
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
      case 'tool': {
        const tool = this.tool(String(e.id), String(e.name ?? ''))
        tool.status = String(e.status ?? 'ok')
        tool.content = String(e.content ?? '')
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
        break
      }
      case 'compaction':
        this.closeText()
        this.items.push({ kind: 'note', key: this.key(), text: '上下文快满了：用交接说明代替了前面的对话' })
        break
      case 'mode':
        this.mode = e.mode as AgentMode
        this.items.push({ kind: 'note', key: this.key(), text: `权限模式：${MODE_NOTE[String(e.mode)] ?? e.mode}` })
        break
      case 'turn_end': {
        this.closeText()
        const t = this.turn
        this.items.push({
          kind: 'end',
          key: this.key(),
          reason: String(e.reason ?? ''),
          error: e.error ? String(e.error) : undefined,
          steps: t.steps,
          seconds: t.start && e.t ? Math.max(0, Number(e.t) - t.start) : 0,
          prompt: t.prompt,
          hit: t.hit,
          completion: t.completion,
          estimated: t.estimated
        })
        // 还在等的卡片（被打断时）不再转圈
        for (const tool of this.tools.values()) if (['pending', 'running', 'asking'].includes(tool.status)) tool.status = 'interrupted'
        this.running = false
        break
      }
    }
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
    done: '做完了',
    max_steps: '步数到上限了，说「继续」接着做',
    rejected: '停下来了，等你说接下来怎么做',
    timeout: '一直没等到你的回答，先停下了',
    interrupted: '被打断了，说「继续」接着做',
    error: `出错了${end.error ? `：${end.error}` : ''}`
  }
  return words[end.reason] ?? end.reason
}

export function endStats(end: EndItem): string {
  const parts = [`${end.steps} 步`, `${end.seconds.toFixed(1)} 秒`]
  if (end.prompt) {
    parts.push(`输入 ${compact(end.prompt)}（命中缓存 ${Math.round((end.hit / end.prompt) * 100)}%${end.estimated ? '，估算' : ''}）`)
    parts.push(`输出 ${compact(end.completion)}`)
  }
  return parts.join(' · ')
}
