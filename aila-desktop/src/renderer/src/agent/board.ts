/**
 * 任务看板：几个任务同时做时，一眼看到每个在干嘛、哪个在等你、哪个做完了、哪个卡住了。
 * 纯函数（分栏、状态那一行字、一次开几个任务的拆分），界面在 TaskBoard.vue。
 */
import type { AgentSessionSummary } from '../../../shared/agent'
import { t, localized } from '../i18n'

export type Column = 'waiting' | 'running' | 'done' | 'stuck'

export const COLUMNS: { id: Column; label: string; hint: string }[] = localized([
  { id: 'waiting', label: '等你', hint: '要你同意、看计划、或者计划停下了' },
  { id: 'running', label: '在做', hint: '她正在做' },
  { id: 'done', label: '做完了', hint: '最近三天做完的' },
  { id: 'stuck', label: '卡住了', hint: '出错、步数用完、被打断、花费到上限' }
])

export const OUTCOME_TEXT: Record<string, string> = localized({
  done: '做完了',
  error: '出错了',
  max_steps: '步数用完了',
  interrupted: '被打断了',
  budget: '花费到上限了',
  rejected: '你拒绝以后停下了'
})

const DAY = 86400

/** 一个会话放哪一栏；没开始做过、或者做完很久了的不放（null） */
export function columnOf(s: AgentSessionSummary, now: number, days = 3): Column | null {
  if (s.waiting) return 'waiting'
  if (s.running) return 'running'
  if (!s.outcome) return null
  if ((s.updated ?? 0) < now - days * DAY) return null
  return s.outcome === 'done' ? 'done' : 'stuck'
}

/** 分栏；每栏里最近更新的在前。now 是秒 */
export function boardColumns(list: AgentSessionSummary[], now: number): Record<Column, AgentSessionSummary[]> {
  const out: Record<Column, AgentSessionSummary[]> = { waiting: [], running: [], done: [], stuck: [] }
  for (const s of list) {
    const col = columnOf(s, now)
    if (col) out[col].push(s)
  }
  for (const col of Object.values(out)) col.sort((a, b) => (b.updated ?? 0) - (a.updated ?? 0))
  return out
}

const VERBS: Record<string, string> = localized({
  read_file: '读',
  edit_file: '改',
  write_file: '写',
  run_command: '跑',
  list_files: '找文件',
  search: '搜',
  explore: '派助手查',
  web_search: '搜网页',
  web_fetch: '读网页',
  delegate: '派助手做',
  merge_work: '收回改动',
  todo_write: '更新待办',
  code_search: '按意思搜',
  code_locate: '按报错定位',
  propose_plan: '交计划',
  remember: '记下',
  job_output: '看后台输出'
})

/** 卡片上那一行：在做 → 「在改 cart.py」；等你 → 等什么；做完 / 卡住 → 结果 */
export function statusLine(s: AgentSessionSummary): string {
  if (s.waiting === 'confirm') return s.pending ? t('等你同意：{action}', { action: s.pending.action }) : t('等你同意一个操作')
  if (s.waiting === 'plan') return t('交了一份计划，等你决定')
  if (s.waiting === 'paused') return t('计划做到一半停下了，等你说继续')
  if (s.running) {
    const a = s.activity
    if (!a) return t('在想……')
    const verb = VERBS[a.tool] ?? (a.tool.startsWith('browser_') ? t('看网页') : a.tool.startsWith('mcp__') ? t('用外部工具') : a.tool)
    return a.target ? t('在{verb} {target}', { verb, target: a.target }) : t('在{verb}', { verb })
  }
  const result = OUTCOME_TEXT[s.outcome ?? ''] ?? s.outcome ?? ''
  return s.edits ? `${result} · ${t('改了 {n} 个文件', { n: s.edits })}` : result
}

export const MAX_BATCH = 6

/** 一次开几个任务：一行一个，空行不算；太多了报错（同时跑太多，确认和花费都看不过来） */
export function parseBatch(text: string): string[] {
  const tasks = text
    .split(/\r?\n/)
    .map((l) => l.replace(/^\s*(?:[-*•]|\d+[.、)])\s*/, '').trim())
    .filter(Boolean)
  if (tasks.length > MAX_BATCH) throw new Error(t('一次最多开 {n} 个，同时做太多确认和花费都看不过来。', { n: MAX_BATCH }))
  return tasks
}
