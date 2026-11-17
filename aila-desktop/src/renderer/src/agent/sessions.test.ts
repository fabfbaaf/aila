import { describe, expect, it } from 'vitest'
import type { AgentSessionSummary } from '../../../shared/agent'
import { deleteNotice, groupSessions } from './sessions'

function s(id: string, updated: Date): AgentSessionSummary {
  return {
    id,
    cwd: 'C:/p',
    title: id,
    mode: 'ask',
    model: 'm',
    updated: updated.getTime() / 1000,
    events: 1,
    usage: { prompt: 0, completion: 0, cache_hit: 0, cache_miss: 0 }
  }
}

describe('groupSessions', () => {
  const now = new Date(2026, 10, 18, 15, 0).getTime() // 周三下午

  it('按今天、昨天、这周、更早分组，空组不出现，组内顺序不变', () => {
    const list = [
      s('a', new Date(2026, 10, 18, 14, 0)),
      s('b', new Date(2026, 10, 18, 0, 5)),
      s('c', new Date(2026, 10, 17, 23, 59)),
      s('d', new Date(2026, 10, 13, 9, 0)),
      s('e', new Date(2026, 9, 1))
    ]
    const groups = groupSessions(list, now)
    expect(groups.map((g) => [g.label, g.items.map((x) => x.id).join('')])).toEqual([
      ['今天', 'ab'],
      ['昨天', 'c'],
      ['这周', 'd'],
      ['更早', 'e']
    ])
    expect(groupSessions([s('x', new Date(2026, 0, 1))], now).map((g) => g.label)).toEqual(['更早'])
    expect(groupSessions([], now)).toEqual([])
  })

  it('没有更新时间的算更早', () => {
    const odd = { ...s('z', new Date(now)), updated: undefined }
    expect(groupSessions([odd], now)[0].label).toBe('更早')
  })
})

describe('deleteNotice', () => {
  it('在分支上做的会话说明临时目录会放弃', () => {
    expect(deleteNotice({ title: 't', branch: null }).join('')).not.toContain('分支')
    const lines = deleteNotice({ title: 't', branch: 'aila/fix-1' })
    expect(lines.join('')).toContain('aila/fix-1')
    expect(lines.at(-1)).toBe('你项目里的文件不会动。')
  })
})
