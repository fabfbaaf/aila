import { describe, expect, it } from 'vitest'
import { cleanPrefs } from './desktop'
import { cleanRoute, parseHash, toHash } from './shell'
import {
  daysKnown,
  diaryDue,
  diaryFacts,
  lateNight,
  nextWorkAt,
  occasionLine,
  occasionOf,
  workDue,
  WORK_FORCE_AFTER_MS,
  WORK_IDLE_SECONDS
} from './studio'

const DAY = 86_400_000

describe('她的作品什么时候做', () => {
  it('每个周期里随机落一个时刻（周期的 60%～140%）', () => {
    expect(nextWorkAt(0, 2, 0)).toBe(2 * DAY * 0.6)
    expect(nextWorkAt(0, 2, 1)).toBe(2 * DAY * 1.4)
    expect(nextWorkAt(0, 7, 0.5)).toBe(7 * DAY)
  })

  it('到点了还要你不在忙；一直不空下来，过几个小时也照做', () => {
    const state = { nextWorkAt: 1000 }
    expect(workDue({}, 5000, 9999)).toBe(false)
    expect(workDue(state, 999, 9999)).toBe(false)
    expect(workDue(state, 2000, 10)).toBe(false)
    expect(workDue(state, 2000, WORK_IDLE_SECONDS)).toBe(true)
    expect(workDue(state, 1000 + WORK_FORCE_AFTER_MS, 0)).toBe(true)
  })

  it('设置：只认给定的几个选项，默认不让她做', () => {
    expect(cleanPrefs({}).worksEvery).toBe(0)
    expect(cleanPrefs({ worksEvery: 3, worksBudget: 1, diary: false })).toMatchObject({ worksEvery: 3, worksBudget: 1, diary: false })
    expect(cleanPrefs({ worksEvery: 5, worksBudget: 100 })).toMatchObject({ worksEvery: 0, worksBudget: 0.5 })
  })
})

describe('特别的日子', () => {
  const at = (s: string): Date => new Date(`${s}T10:00:00`)

  it('认识第几天：第一次见面那天是第 1 天', () => {
    expect(daysKnown('2026-01-01', at('2026-01-01'))).toBe(1)
    expect(daysKnown('2026-01-01', at('2026-04-10'))).toBe(100)
    expect(daysKnown(undefined, at('2026-01-01'))).toBe(0)
  })

  it('第 100、200 天、周年、节日', () => {
    expect(occasionOf(at('2026-04-10'), '2026-01-01')).toBe('认识第 100 天')
    expect(occasionOf(at('2027-01-01'), '2026-01-01')).toBe('元旦')
    expect(occasionOf(at('2026-12-31'), '2026-01-01')).toBe('认识 1 周年')
    expect(occasionOf(at('2026-03-03'), '2026-01-01')).toBe('')
    expect(occasionOf(at('2026-10-24'), undefined)).toBe('程序员节')
    expect(occasionLine('认识第 100 天')).toContain('我们认识第 100 天')
    expect(occasionLine('')).toBe('')
  })
})

describe('日记和深夜', () => {
  it('晚上十点以后写一篇，一天一篇', () => {
    expect(diaryDue({}, new Date('2026-11-18T21:59:00'), '2026-11-18')).toBe(false)
    expect(diaryDue({}, new Date('2026-11-18T22:00:00'), '2026-11-18')).toBe(true)
    expect(diaryDue({ diary: '2026-11-18' }, new Date('2026-11-18T23:00:00'), '2026-11-18')).toBe(false)
  })

  it('深夜是 23:30 到凌晨 4 点', () => {
    expect(lateNight(new Date('2026-11-18T23:29:00'))).toBe(false)
    expect(lateNight(new Date('2026-11-18T23:30:00'))).toBe(true)
    expect(lateNight(new Date('2026-11-19T03:59:00'))).toBe(true)
    expect(lateNight(new Date('2026-11-19T04:00:00'))).toBe(false)
  })

  it('日记的材料都是数出来的', () => {
    const facts = diaryFacts({ tasks: ['修分页', '加优惠券'], messages: 12, reminders: 1, daysKnown: 30, occasion: '' })
    expect(facts).toEqual([
      '今天是我们认识的第 30 天',
      '他让我做了 2 个编程任务：「修分页」、「加优惠券」',
      '我们聊了 12 句',
      '我提醒了他 1 件事'
    ])
    expect(diaryFacts({ tasks: [], messages: 0, reminders: 0, daysKnown: 0, occasion: '圣诞节' })).toEqual(['今天是圣诞节', '他今天没怎么找我'])
  })
})

describe('作品页的地址', () => {
  it('#/studio 和 #/studio/<作品>', () => {
    expect(parseHash('#/studio')).toEqual({ page: 'studio' })
    expect(parseHash('#/studio/20261118-010203-abcd')).toEqual({ page: 'studio', work: '20261118-010203-abcd' })
    expect(parseHash('#/studio/..')).toEqual({ page: 'studio' })
    expect(toHash({ page: 'studio', work: '20261118-010203-abcd' })).toBe('/studio/20261118-010203-abcd')
    expect(cleanRoute({ page: 'studio', work: 'x/../y' })).toEqual({ page: 'studio' })
  })
})
