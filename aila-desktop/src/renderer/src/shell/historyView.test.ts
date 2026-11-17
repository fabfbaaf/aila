import { describe, expect, it } from 'vitest'
import type { StoredMessage } from '../../../shared/cloud'
import { cleanText, dayLabel, groupHistory } from './historyView'

const msg = (id: string, at: string, content: string, extra: Partial<StoredMessage> = {}): StoredMessage => ({
  id,
  role: 'user',
  content,
  createdAt: new Date(at).toISOString(),
  ...extra
})

const NOW = new Date(2026, 10, 16, 20, 0)

describe('对话记录页', () => {
  it('按天分组：新的一天在上面，一天之内按时间先后；删掉的不显示', () => {
    const groups = groupHistory(
      [
        msg('c', '2026-11-16T09:00:00', '早上好'),
        msg('a', '2026-11-14T10:00:00', '三天前'),
        msg('b', '2026-11-16T08:00:00', '更早一点'),
        msg('x', '2026-11-16T10:00:00', '删掉的', { deleted: true }),
        msg('y', '2026-11-15T23:59:00', '昨天深夜')
      ],
      '',
      NOW
    )
    expect(groups.map((g) => g.label)).toEqual(['今天', '昨天', '11 月 14 日（星期六）'])
    expect(groups[0].items.map((m) => m.id)).toEqual(['b', 'c'])
  })

  it('按关键词筛（不分大小写）', () => {
    const groups = groupHistory(
      [msg('a', '2026-11-16T09:00:00', '帮我看看 Spring Boot'), msg('b', '2026-11-16T09:01:00', '好的')],
      'spring',
      NOW
    )
    expect(groups).toHaveLength(1)
    expect(groups[0].items.map((m) => m.id)).toEqual(['a'])
    expect(groupHistory([msg('a', '2026-11-16T09:00:00', 'x')], '没有', NOW)).toEqual([])
  })

  it('她的话里的表情、动作标签不显示', () => {
    expect(cleanText('[happy]你好。……我是艾拉。[nod]今天也请多指教。')).toBe('你好。……我是艾拉。今天也请多指教。')
    expect(cleanText('数组 a[i] 和 [链接]')).toBe('数组 a[i] 和 [链接]')
  })

  it('去年的日期带年份', () => {
    expect(dayLabel(new Date(2025, 0, 2), NOW)).toBe('2025 年 1 月 2 日（星期四）')
  })
})
