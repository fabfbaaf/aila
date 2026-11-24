import { describe, expect, it } from 'vitest'
import type { AgentSessionSummary } from '../shared/agent'
import { reviewDue, weekOf } from '../shared/review'
import { reviewBody } from './weekReview'

describe('哪一周、什么时候写', () => {
  it('ISO 周：周一开始，跨年按周四算', () => {
    expect(weekOf(new Date(2026, 10, 23))).toEqual({ week: '2026-W48', start: '2026-11-23', end: '2026-11-29' })
    expect(weekOf(new Date(2026, 10, 29, 23))).toMatchObject({ week: '2026-W48' }) // 周日还是这周
    expect(weekOf(new Date(2027, 0, 1))).toMatchObject({ week: '2026-W53', start: '2026-12-28' }) // 2027-01-01 是周五
  })

  it('周五 18 点以后、周末写，一周一次', () => {
    expect(reviewDue(undefined, new Date(2026, 10, 27, 17, 59))).toBe(false) // 周五下午
    expect(reviewDue(undefined, new Date(2026, 10, 27, 18, 0))).toBe(true)
    expect(reviewDue(undefined, new Date(2026, 10, 29, 9))).toBe(true) // 周日
    expect(reviewDue('2026-W48', new Date(2026, 10, 28))).toBe(false) // 写过了
    expect(reviewDue(undefined, new Date(2026, 10, 24, 20))).toBe(false) // 周二晚上
  })
})

describe('回顾的材料', () => {
  const now = new Date(2026, 10, 27, 19) // 周五晚上
  const at = (d: Date): number => Math.floor(d.getTime() / 1000)
  const s = (over: Partial<AgentSessionSummary>): AgentSessionSummary =>
    ({ id: 'x', cwd: 'C:\\work\\shop', title: 't', mode: 'ask', model: 'm', events: 1, usage: {}, outcome: 'done', ...over }) as AgentSessionSummary

  it('这周的任务（做完没有）、聊了几句、下周约好的事', () => {
    const body = reviewBody({
      now,
      sessions: [
        s({ title: '加运费', updated: at(new Date(2026, 10, 25)) }),
        s({ title: '重构登录', updated: at(new Date(2026, 10, 26)), waiting: 'confirm' }),
        s({ title: '上周的', updated: at(new Date(2026, 10, 20)) }),
        s({ title: '她的作品', cwd: 'C:\\data\\works\\20261125-101010-abcd', updated: at(new Date(2026, 10, 25)) })
      ],
      messages: [
        { id: '1', role: 'user', content: 'a', createdAt: new Date(2026, 10, 24).toISOString() },
        { id: '2', role: 'assistant', content: 'b', createdAt: new Date(2026, 10, 24).toISOString() },
        { id: '3', role: 'user', content: 'c', createdAt: new Date(2026, 10, 10).toISOString() }
      ],
      reminders: [
        { id: 'r1', at: new Date(2026, 10, 30, 10).toISOString(), text: '和产品对需求', createdAt: '' },
        { id: 'r2', at: new Date(2026, 11, 20).toISOString(), text: '太远了', createdAt: '' }
      ],
      memories: [{ id: 'm', content: '用户下周二面试', kind: 'event', expiresAt: new Date(2026, 11, 1, 23).toISOString(), createdAt: '', updatedAt: '' }]
    })
    expect(body.week).toBe('2026-W48')
    expect(body.tasks).toEqual([
      { title: '加运费', project: 'C:\\work\\shop', done: true },
      { title: '重构登录', project: 'C:\\work\\shop', done: false }
    ])
    expect(body.chats).toBe(1)
    expect(body.upcoming).toEqual(['11 月 30 日 10:00 提醒：和产品对需求', '用户下周二面试'])
  })
})
