import { describe, expect, it } from 'vitest'
import type { AgentSessionSummary } from '../shared/agent'
import type { StoredMemory, StoredMessage } from '../shared/cloud'
import { dailyReport, due, meetingReminder, morningBriefing, workday, type BriefingInput } from './briefing'

const at = (y: number, mo: number, d: number, h: number, mi = 0): Date => new Date(y, mo - 1, d, h, mi)
const NOW = at(2026, 11, 17, 9, 30)

function session(id: string, title: string, updated: Date, extra: Partial<AgentSessionSummary> = {}): AgentSessionSummary {
  return {
    id,
    cwd: 'D:/work/shop',
    title,
    mode: 'ask',
    model: 'deepseek-chat',
    events: 10,
    updated: updated.getTime() / 1000,
    usage: { prompt: 10000, completion: 500, cache_hit: 8000, cache_miss: 2000 },
    ...extra
  }
}

function memory(content: string, expiresAt: Date | null, kind: StoredMemory['kind'] = 'event'): StoredMemory {
  return { id: content, content, kind, expiresAt: expiresAt?.toISOString() ?? null, createdAt: '', updatedAt: '' }
}

const input = (over: Partial<BriefingInput> = {}): BriefingInput => ({
  now: NOW,
  sessions: [],
  memories: [],
  reminders: [],
  messages: [],
  ...over
})

describe('每天第一次见面', () => {
  it('说昨天的任务、没合并的分支、今天的日程和提醒', () => {
    const r = morningBriefing(
      input({
        sessions: [
          session('a', '修购物车总价', at(2026, 11, 16, 15)),
          session('b', '加打折函数', at(2026, 11, 16, 17), { branch: 'aila/task-1' }),
          session('c', '很久以前的', at(2026, 11, 10, 9))
        ],
        memories: [memory('用户今天下午三点面试', at(2026, 11, 17, 23)), memory('用户喜欢美式', null, 'preference')],
        reminders: [{ id: 'r', at: at(2026, 11, 17, 14).toISOString(), text: '交周报', createdAt: '' }]
      })
    )
    expect(r.detail).toBe(true)
    expect(r.text).toBe(
      '早上好。昨天一起做了 2 个任务：「修购物车总价」、「加打折函数」。「加打折函数」还在分支上，没合并。今天：你今天下午三点面试；1 个提醒。'
    )
  })

  it('日历上今天的会：说前两个、多的说个数，已经开完的不说；会前提醒那一句', () => {
    const ev = (title: string, h: number, end = h + 1, allDay = false) => ({
      uid: title, title, start: at(2026, 11, 17, h).toISOString(), end: at(2026, 11, 17, end).toISOString(), allDay, location: ''
    })
    const meetings = [ev('早会', 8, 9), ev('周会', 10), ev('评审', 14), ev('一对一', 16), ev('团建', 0, 24, true)]
    const r = morningBriefing(input({ meetings }), false)
    expect(r.detail).toBe(true)
    expect(r.text).toBe('今天：10:00 周会；14:00 评审；一共 4 个会。')
    expect(meetingReminder(ev('周会', 10), at(2026, 11, 17, 9, 52))).toBe('8 分钟后：周会（10:00）')
  })

  it('什么都没有就只打招呼；刚启动时（已经打过招呼）不再说早上好', () => {
    expect(morningBriefing(input())).toEqual({ text: '早上好。', detail: false })
    const s = [session('a', '修购物车总价', at(2026, 11, 16, 15))]
    expect(morningBriefing(input({ sessions: s }), false).text).toBe('昨天一起做了 1 个任务：「修购物车总价」。')
  })

  it('特别的日子、她昨天做的作品、明天的事、前几天的事后来怎么样；她做作品的会话不算你的任务', () => {
    const work = {
      id: 'w', title: '像素时钟', idea: '', kind: 'web' as const, why: '', occasion: '', session: 's', status: 'done' as const,
      reason: '', entry: 'index.html', announced: true, created: at(2026, 11, 16, 22).getTime() / 1000
    }
    const r = morningBriefing(
      input({
        occasion: '认识第 100 天',
        works: [work, { ...work, id: 'x', title: '没做成的', status: 'failed' }],
        sessions: [session('s', '像素时钟', at(2026, 11, 16, 22), { cwd: 'C:/data/works/20261116-220000-abcd' })],
        memories: [memory('用户明天上午面试', at(2026, 11, 18, 11)), memory('用户周日去看演唱会', at(2026, 11, 15, 20))]
      }),
      false
    )
    expect(r.text).toBe(
      '……今天是我们认识第 100 天。谢谢你一直让我待在这里。昨天我自己做了个小东西「像素时钟」，放在「作品」里了。' +
        '明天：你明天上午面试……准备得怎么样？之前说的「你周日去看演唱会」，后来怎么样了？'
    )
    expect(r.detail).toBe(true)
  })

  it('凌晨 5 点前还算前一天', () => {
    expect(workday(at(2026, 11, 17, 2))).toBe('2026-11-16')
    expect(workday(at(2026, 11, 17, 6))).toBe('2026-11-17')
  })
})

describe('晚上的日报', () => {
  it('今天的任务数、没合并的、token、命中率、聊了几句', () => {
    const night = at(2026, 11, 17, 18, 5)
    const msgs: StoredMessage[] = [
      { id: '1', role: 'user', content: '在吗', createdAt: at(2026, 11, 17, 10).toISOString() },
      { id: '2', role: 'assistant', content: '在', createdAt: at(2026, 11, 17, 10).toISOString() },
      { id: '3', role: 'user', content: '昨天的', createdAt: at(2026, 11, 16, 10).toISOString() }
    ]
    const text = dailyReport(
      input({
        now: night,
        sessions: [
          session('a', '一', at(2026, 11, 17, 11)),
          session('b', '二', at(2026, 11, 17, 15), { branch: 'aila/task-2' }),
          session('c', '昨天', at(2026, 11, 16, 15))
        ],
        messages: msgs
      })
    )
    expect(text).toBe('今天一起做了 2 个任务（1 个还在分支上没合并）。用了 2.1 万个 token，输入里 80% 命中了缓存。和我聊了 1 句。……今天辛苦了。')
  })

  it('今天什么都没做', () => {
    expect(dailyReport(input({ now: at(2026, 11, 17, 18) }))).toBe('今天还没一起做什么……明天见。')
  })
})

describe('什么时候说', () => {
  it('今天说过就不说；日报过了点才说', () => {
    expect(due(null, NOW)).toBe(true)
    expect(due('2026-11-17', NOW)).toBe(false)
    expect(due('2026-11-16', NOW)).toBe(true)
    expect(due(null, at(2026, 11, 17, 17, 59), '18:00')).toBe(false)
    expect(due(null, at(2026, 11, 17, 18, 0), '18:00')).toBe(true)
    expect(due('2026-11-17', at(2026, 11, 17, 19), '18:00')).toBe(false)
  })
})
