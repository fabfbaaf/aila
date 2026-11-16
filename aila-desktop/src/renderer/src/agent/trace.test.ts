import { describe, expect, it } from 'vitest'
import type { AgentEvent } from '../../../shared/agent'
import { buildTrace, slowest } from './trace'

const ev = (type: string, extra: Record<string, unknown> = {}): AgentEvent => ({ type, ...extra })

const EVENTS: AgentEvent[] = [
  ev('start', { seq: 0, t: 99 }),
  ev('user', { seq: 1, t: 100, text: '修好运费' }),
  ev('assistant', { seq: 2, t: 102, text: '', timing: { first: 0.8, total: 2 },
    tool_calls: [{ id: 'a', name: 'read_file', arguments: '{"path":"cart.py"}' }] }),
  ev('usage', { seq: 3, t: 102, prompt: 3000, cache_hit: 0, completion: 40 }),
  ev('tool', { seq: 4, t: 102.1, id: 'a', name: 'read_file', status: 'ok', seconds: 0.1 }),
  ev('assistant', { seq: 5, t: 105, text: '', timing: { first: 1.5, total: 2.9 },
    tool_calls: [{ id: 'b', name: 'edit_file', arguments: '{"path":"cart.py"}' }] }),
  ev('usage', { seq: 6, t: 105, prompt: 3400, cache_hit: 3000, completion: 80 }),
  ev('confirm', { seq: 7, t: 105, id: 'c1', action: '改 cart.py' }),
  ev('confirm_end', { seq: 8, t: 111, id: 'c1', result: 'approved' }),
  ev('tool', { seq: 9, t: 111.05, id: 'b', name: 'edit_file', status: 'ok', seconds: 0.05 }),
  ev('assistant', { seq: 10, t: 113, text: '改好了。', timing: { first: 1, total: 1.95 }, tool_calls: [] }),
  ev('usage', { seq: 11, t: 113, prompt: 3600, cache_hit: 3400, completion: 20 }),
  ev('turn_end', { seq: 12, t: 113, reason: 'done' })
]

describe('执行轨迹', () => {
  it('一轮排成时间线：模型请求、工具、等你确认', () => {
    const [turn] = buildTrace(EVENTS)
    expect(turn.text).toBe('修好运费')
    expect(turn.duration).toBe(13)
    expect(turn.steps).toBe(3)
    expect(turn.rows.map((r) => `${r.kind}:${r.label}`)).toEqual([
      'model:想下一步 → 读 cart.py',
      'tool:读 cart.py',
      'model:想下一步 → 改 cart.py',
      'wait:等你确认：改 cart.py',
      'tool:改 cart.py',
      'model:回答'
    ])
    const first = turn.rows[0]
    expect([first.start, first.end, first.first]).toEqual([0, 2, 0.8])
    expect(first.tokens).toEqual({ prompt: 3000, hit: 0, completion: 40 })
    const wait = turn.rows[3]
    expect([wait.start, wait.end, wait.status]).toEqual([5, 11, 'approved'])
    expect([turn.prompt, turn.hit]).toEqual([10000, 6400])
    expect(turn.running).toBe(false)
  })

  it('最慢的是等你确认的那 6 秒', () => {
    const [turn] = buildTrace(EVENTS)
    expect(slowest(turn, 1)[0].kind).toBe('wait')
  })

  it('还在做的一轮拉到现在；旧日志没有计时也能画', () => {
    const old = EVENTS.slice(0, 5).map((e) => {
      const copy = { ...e }
      delete copy.timing
      delete copy.seconds
      return copy
    })
    const [turn] = buildTrace(old, 110)
    expect(turn.running).toBe(true)
    expect(turn.duration).toBe(10)
    expect(turn.rows[0].start).toBe(0) // 没有计时：从上一件事结束算起
    expect(turn.rows[1].start).toBe(2)
  })
})
