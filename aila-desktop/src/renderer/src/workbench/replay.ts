/**
 * 在星图上回放她的一次任务：把会话日志变成一步一步（读了哪个文件、改了哪个、跑了什么命令、这一轮怎么结束的），
 * 每一步对到星图的星球上。纯函数，界面在 WorkbenchApp.vue（时间轴、播放、拖动）。
 *
 * 为什么按「步」不按真实时间播：真实时间里她想了 20 秒、跑测试跑了 1 分钟，按真实时间看大半时间什么都不动；
 * 按步播，每一步的停留一样长，拖动也好对。
 */
import type { AgentEvent, AgentSessionSummary } from '../../../shared/agent'
import { starOf } from './layout'
import { localized } from '../i18n'

export type StepKind = 'ask' | 'read' | 'edit' | 'run' | 'search' | 'explore' | 'other' | 'end'

export interface ReplayStep {
  seq: number
  kind: StepKind
  label: string
  /** 对到星图上的星球（项目外的文件、不是文件的步骤是 null） */
  star: string | null
  ok: boolean
}

const KIND: Record<string, StepKind> = {
  read_file: 'read',
  edit_file: 'edit',
  write_file: 'edit',
  run_command: 'run',
  list_files: 'search',
  search: 'search',
  code_search: 'search',
  code_locate: 'search',
  code_symbol: 'search',
  explore: 'explore',
  web_search: 'search',
  web_fetch: 'read',
  delegate: 'explore',
  merge_work: 'edit'
}

const VERB: Record<StepKind, string> = localized({
  ask: '你说',
  read: '读',
  edit: '改',
  run: '跑',
  search: '找',
  explore: '派助手查',
  other: '用',
  end: ''
})

const END: Record<string, string> = localized({
  done: '这一轮做完了',
  error: '这一轮出错了',
  max_steps: '步数用完了',
  interrupted: '被打断了',
  budget: '花费到上限了',
  rejected: '你拒绝以后停下了'
})

function argsOf(raw: unknown): Record<string, unknown> {
  if (typeof raw !== 'string') return {}
  try {
    const v = JSON.parse(raw) as unknown
    return v && typeof v === 'object' ? (v as Record<string, unknown>) : {}
  } catch {
    return {}
  }
}

/** 会话日志 → 回放的每一步。root：星图项目的根目录；cwd：会话的目录（星球 id 相对 root） */
export function buildReplay(events: AgentEvent[], root: string, cwd: string): ReplayStep[] {
  const calls = new Map<string, { name: string; args: Record<string, unknown> }>()
  const steps: ReplayStep[] = []
  for (const e of events) {
    const seq = Number(e.seq ?? steps.length)
    if (e.type === 'user') {
      const text = String(e.title ?? e.text ?? '').replace(/\s+/g, ' ').trim()
      steps.push({ seq, kind: 'ask', label: text.length > 60 ? `${text.slice(0, 60)}……` : text, star: null, ok: true })
    } else if (e.type === 'assistant' && Array.isArray(e.tool_calls)) {
      for (const c of e.tool_calls as { id: string; name: string; arguments?: string }[]) {
        calls.set(c.id, { name: c.name, args: argsOf(c.arguments) })
      }
    } else if (e.type === 'tool') {
      const call = calls.get(String(e.id))
      const name = String(e.name ?? call?.name ?? '')
      const args = call?.args ?? {}
      const kind = KIND[name] ?? 'other'
      const path = typeof e.file === 'string' ? e.file : typeof args.path === 'string' ? args.path : ''
      const target = path || String(args.command ?? args.pattern ?? args.query ?? args.task ?? '')
      const short = target.length > 50 ? `${target.slice(0, 50)}……` : target
      steps.push({
        seq,
        kind,
        label: `${VERB[kind]}${kind === 'other' ? ` ${name}` : ''}${short ? ` ${short}` : ''}`,
        star: path ? starOf(root, cwd, path) : null,
        ok: e.status === 'ok'
      })
    } else if (e.type === 'turn_end') {
      steps.push({ seq, kind: 'end', label: END[String(e.reason)] ?? String(e.reason ?? ''), star: null, ok: e.reason === 'done' })
    }
  }
  return steps
}

/** 到第 i 步为止（含）改过的星球：拖动时间轴时用它重新标绿 */
export function editedUpTo(steps: ReplayStep[], i: number): Set<string> {
  const out = new Set<string>()
  for (const s of steps.slice(0, i + 1)) if (s.kind === 'edit' && s.ok && s.star) out.add(s.star)
  return out
}

/** 这个项目里能回放的任务：会话目录在项目根目录下（不分大小写和斜杠方向），最近的在前 */
export function sessionsIn(root: string, list: AgentSessionSummary[]): AgentSessionSummary[] {
  const norm = (p: string): string => p.replace(/\\/g, '/').replace(/\/+$/, '').toLowerCase()
  const r = norm(root)
  return list
    .filter((s) => s.kind !== 'computer' && (norm(s.cwd) === r || norm(s.cwd).startsWith(`${r}/`)) && s.events > 2)
    .sort((a, b) => (b.updated ?? 0) - (a.updated ?? 0))
}
