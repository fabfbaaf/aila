/**
 * 执行轨迹：把一轮里的事件排成时间线（纯逻辑，可以单测）。
 *
 * 每一行是一段时间：
 * - model：一次模型请求（first 之前在等第一个字，之后在出字），带这一步的 token 和缓存命中
 * - tool：一个工具调用（读文件、跑命令……），带状态
 * - wait：等你确认
 *
 * 时间都来自会话日志：事件的 t（写进日志的时刻），加上 assistant 事件里的 timing（这一步多久、多久出第一个字）、
 * tool 事件里的 seconds（工具跑了多久）。重新打开会话也能画出来。
 */
import type { AgentEvent, AgentUndoEvent } from '../../../shared/agent'
import { actionOf, readUndo } from './fold'
import { t as tr } from '../i18n'

export interface TraceRow {
  kind: 'model' | 'tool' | 'wait'
  label: string
  /** 相对这一轮开始的秒数 */
  start: number
  end: number
  status?: string
  /** model：开始后多久收到第一个字 */
  first?: number
  tokens?: { prompt: number; hit: number; completion: number }
}

export interface TraceTurn {
  key: string
  text: string
  duration: number
  rows: TraceRow[]
  reason: string
  running: boolean
  steps: number
  prompt: number
  hit: number
  completion: number
  undo?: AgentUndoEvent
}

function parseArgs(raw: unknown): Record<string, unknown> {
  try {
    const v = JSON.parse(String(raw || '{}'))
    return v && typeof v === 'object' && !Array.isArray(v) ? v : {}
  } catch {
    return {}
  }
}

export function buildTrace(events: AgentEvent[], now?: number): TraceTurn[] {
  const turns: TraceTurn[] = []
  let turn: TraceTurn | null = null
  let origin = 0
  let cursor = 0
  let lastModel: TraceRow | null = null
  const calls = new Map<string, { name: string; args: Record<string, unknown> }>()
  const confirms = new Map<string, { start: number; label: string }>()

  for (const e of events) {
    const t = typeof e.t === 'number' ? e.t : null
    if (e.type === 'user') {
      if (t === null) continue
      origin = t
      cursor = t
      turn = { key: `turn-${e.seq ?? turns.length}`, text: String(e.text ?? ''), duration: 0, rows: [], reason: '',
        running: true, steps: 0, prompt: 0, hit: 0, completion: 0 }
      turns.push(turn)
      continue
    }
    if (e.type === 'undo') {
      const result = readUndo(e)
      const target = result && turns.find((item) => item.key === `turn-${result.turn_seq}`)
      if (target) target.undo = result
      continue // 用户随后做的撤销不算在执行用时里
    }
    if (!turn || t === null) continue
    const rel = (x: number): number => Math.max(0, x - origin)
    switch (e.type) {
      case 'assistant': {
        const timing = e.timing as { first?: number; total?: number } | undefined
        const start = timing?.total !== undefined ? Math.max(cursor, t - timing.total) : cursor
        const toolCalls = (e.tool_calls as { id: string; name: string; arguments: string }[]) ?? []
        for (const c of toolCalls) calls.set(c.id, { name: c.name, args: parseArgs(c.arguments) })
        lastModel = {
          kind: 'model',
          label: toolCalls.length ? tr('想下一步 → {what}', { what: toolCalls.map((c) => actionOf(c.name, parseArgs(c.arguments))).join(', ') }) : tr('回答'),
          start: rel(start),
          end: rel(t),
          first: timing?.first
        }
        turn.rows.push(lastModel)
        turn.steps++
        cursor = t
        break
      }
      case 'usage':
        if (e.purpose === 'compaction') break
        if (lastModel && !e.purpose) {
          // 子 Agent 的用量（purpose = explore）只算进这一轮的总数，不挂在主 Agent 的某一步上
          lastModel.tokens = { prompt: Number(e.prompt ?? 0), hit: Number(e.cache_hit ?? 0), completion: Number(e.completion ?? 0) }
        }
        turn.prompt += Number(e.prompt ?? 0)
        turn.hit += Number(e.cache_hit ?? 0)
        turn.completion += Number(e.completion ?? 0)
        break
      case 'confirm':
        confirms.set(String(e.id), { start: t, label: tr('等你确认：{action}', { action: String(e.action ?? '') }) })
        cursor = t
        break
      case 'confirm_end': {
        const c = confirms.get(String(e.id))
        if (c) turn.rows.push({ kind: 'wait', label: c.label, start: rel(c.start), end: rel(t), status: String(e.result ?? '') })
        cursor = t
        break
      }
      case 'tool': {
        const call = calls.get(String(e.id))
        const seconds = typeof e.seconds === 'number' ? e.seconds : null
        const start = seconds !== null ? Math.max(cursor, t - seconds) : cursor
        turn.rows.push({
          kind: 'tool',
          label: actionOf(call?.name ?? String(e.name ?? ''), call?.args ?? {}),
          start: rel(start),
          end: rel(t),
          status: String(e.status ?? 'ok')
        })
        cursor = t
        break
      }
      case 'compaction':
        turn.rows.push({ kind: 'model', label: tr('压缩上下文'), start: rel(cursor), end: rel(t) })
        cursor = t
        break
      case 'turn_end':
        turn.reason = String(e.reason ?? '')
        turn.running = false
        turn.duration = rel(t)
        break
    }
    turn.duration = Math.max(turn.duration, rel(t))
  }
  // 还在做的那一轮：时间轴拉到现在
  if (turn?.running && now) turn.duration = Math.max(turn.duration, now - origin)
  return turns
}

/** 一轮里最慢的几行（时间线下面的提示：卡在哪） */
export function slowest(turn: TraceTurn, n = 3): TraceRow[] {
  return [...turn.rows].sort((a, b) => b.end - b.start - (a.end - a.start)).slice(0, n)
}
