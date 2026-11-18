import { describe, expect, it } from 'vitest'
import type { AgentSessionSummary } from '../../../shared/agent'
import { boardColumns, columnOf, MAX_BATCH, parseBatch, statusLine } from './board'

const NOW = 1_800_000_000

function s(over: Partial<AgentSessionSummary>): AgentSessionSummary {
  return {
    id: over.id ?? 'x',
    cwd: 'C:/code/shop',
    title: '任务',
    mode: 'ask',
    model: 'm',
    events: 3,
    usage: { prompt: 0, completion: 0, cache_hit: 0, cache_miss: 0 },
    updated: NOW - 60,
    ...over
  }
}

describe('看板', () => {
  it('分栏：等你优先，其次在做；做完的按结果分；没开始过、太久以前的不放', () => {
    expect(columnOf(s({ waiting: 'confirm', running: true }), NOW)).toBe('waiting')
    expect(columnOf(s({ running: true }), NOW)).toBe('running')
    expect(columnOf(s({ outcome: 'done' }), NOW)).toBe('done')
    expect(columnOf(s({ outcome: 'max_steps' }), NOW)).toBe('stuck')
    expect(columnOf(s({ outcome: null }), NOW)).toBeNull()
    expect(columnOf(s({ outcome: 'done', updated: NOW - 5 * 86400 }), NOW)).toBeNull()
    const cols = boardColumns([s({ id: 'a', outcome: 'done', updated: NOW - 100 }), s({ id: 'b', outcome: 'done', updated: NOW - 10 })], NOW)
    expect(cols.done.map((x) => x.id)).toEqual(['b', 'a'])
  })

  it('卡片上那一行字', () => {
    expect(statusLine(s({ running: true, activity: { tool: 'edit_file', target: 'cart.py' } }))).toBe('在改 cart.py')
    expect(statusLine(s({ running: true, activity: { tool: 'browser_click', target: '' } }))).toBe('在看网页')
    expect(statusLine(s({ running: true }))).toBe('在想……')
    expect(statusLine(s({ waiting: 'confirm', pending: { id: 'c', tool: 'run_command', action: '跑 npm test', can_remember: true } })))
      .toBe('等你同意：跑 npm test')
    expect(statusLine(s({ outcome: 'done', edits: 2 }))).toBe('做完了 · 改了 2 个文件')
    expect(statusLine(s({ outcome: 'budget' }))).toBe('花费到上限了')
  })

  it('一次开几个：一行一个，去掉列表符号和空行，太多报错', () => {
    expect(parseBatch('- 加优惠券\n\n2. 修分页\n• 补测试\n')).toEqual(['加优惠券', '修分页', '补测试'])
    expect(() => parseBatch(Array.from({ length: MAX_BATCH + 1 }, (_, i) => `任务${i}`).join('\n'))).toThrow('最多')
  })
})
