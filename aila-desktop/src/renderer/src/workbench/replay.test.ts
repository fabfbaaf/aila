import { describe, expect, it } from 'vitest'
import type { AgentEvent, AgentSessionSummary } from '../../../shared/agent'
import { buildReplay, editedUpTo, sessionsIn } from './replay'

const ROOT = 'C:\\code\\shop'

const EVENTS: AgentEvent[] = [
  { type: 'start', seq: 0 },
  { type: 'user', seq: 1, text: '修好运费', title: undefined },
  { type: 'assistant', seq: 2, text: '', tool_calls: [{ id: 'a', name: 'read_file', arguments: '{"path":"src/cart.py"}' }] },
  { type: 'tool', seq: 3, id: 'a', name: 'read_file', status: 'ok' },
  {
    type: 'assistant',
    seq: 4,
    text: '',
    tool_calls: [
      { id: 'b', name: 'edit_file', arguments: '{"path":"src/cart.py","old_string":"x","new_string":"y"}' },
      { id: 'c', name: 'run_command', arguments: '{"command":"python -m pytest -q"}' }
    ]
  },
  { type: 'tool', seq: 5, id: 'b', name: 'edit_file', status: 'ok', file: 'src/cart.py' },
  { type: 'tool', seq: 6, id: 'c', name: 'run_command', status: 'error' },
  { type: 'tool', seq: 7, id: 'd', name: 'read_file', status: 'ok' }, // 找不到参数的也不出错
  { type: 'turn_end', seq: 8, reason: 'done' }
]

describe('回放', () => {
  it('会话日志变成一步一步，文件对到星球上', () => {
    const steps = buildReplay(EVENTS, ROOT, 'C:/code/shop/backend')
    expect(steps.map((s) => s.kind)).toEqual(['ask', 'read', 'edit', 'run', 'read', 'end'])
    expect(steps[1]).toMatchObject({ star: 'backend/src/cart.py', label: '读 src/cart.py', ok: true })
    expect(steps[2]).toMatchObject({ star: 'backend/src/cart.py', label: '改 src/cart.py' })
    expect(steps[3]).toMatchObject({ star: null, label: '跑 python -m pytest -q', ok: false })
    expect(steps[5]).toMatchObject({ label: '这一轮做完了', ok: true })
    expect(steps[0].label).toBe('修好运费')
  })

  it('拖到某一步：到这一步为止改过的文件', () => {
    const steps = buildReplay(EVENTS, ROOT, ROOT)
    expect([...editedUpTo(steps, 1)]).toEqual([])
    expect([...editedUpTo(steps, 2)]).toEqual(['src/cart.py'])
  })

  it('只列这个项目里的任务（含子目录），电脑任务和没做过事的不算', () => {
    const s = (id: string, cwd: string, extra: Partial<AgentSessionSummary> = {}): AgentSessionSummary => ({
      id, cwd, title: id, mode: 'ask', model: '', events: 10, updated: 1, usage: { prompt: 0, completion: 0, cache_hit: 0, cache_miss: 0 }, ...extra
    })
    const list = [
      s('a', 'c:/code/shop/'),
      s('b', 'C:\\code\\shop\\backend', { updated: 5 }),
      s('c', 'C:\\code\\shopping'),
      s('d', 'C:\\code\\shop', { kind: 'computer' }),
      s('e', 'C:\\code\\shop', { events: 1 })
    ]
    expect(sessionsIn(ROOT, list).map((x) => x.id)).toEqual(['b', 'a'])
  })
})
