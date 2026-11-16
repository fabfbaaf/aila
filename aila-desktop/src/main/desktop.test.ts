import { describe, expect, it } from 'vitest'
import { cleanPrefs, DEFAULT_DESKTOP_PREFS } from '../shared/desktop'
import { decidePresence, focusFromQuns } from './presence'
import { BREAK_IDLE_SECONDS, LATE_WINDOW_MS, ReminderBook, SitTimer } from './reminders'

const T0 = Date.parse('2026-11-16T09:00:00+08:00')
const min = (n: number): number => n * 60_000

describe('她什么时候躲开', () => {
  it('系统的状态值', () => {
    expect([1, 2, 3, 4, 5, 6, 7, 0].map(focusFromQuns)).toEqual([
      'away',
      'fullscreen',
      'fullscreen',
      'presentation',
      'normal',
      'normal',
      'fullscreen',
      'normal'
    ])
  })

  it('全屏、演示时藏起来；关了这个开关就留着但不说话', () => {
    const on = { hideOnFullscreen: true }
    const off = { hideOnFullscreen: false }
    expect(decidePresence('fullscreen', false, on)).toEqual({ visible: false, quiet: true, reason: 'fullscreen' })
    expect(decidePresence('presentation', false, off)).toEqual({ visible: true, quiet: true, reason: 'presentation' })
    expect(decidePresence('normal', false, on)).toEqual({ visible: true, quiet: false, reason: 'normal' })
    expect(decidePresence('away', false, on)).toEqual({ visible: true, quiet: true, reason: 'away' })
  })

  it('免打扰优先', () => {
    expect(decidePresence('normal', true, { hideOnFullscreen: false })).toEqual({ visible: false, quiet: true, reason: 'dnd' })
  })

  it('设置页传来的偏好只认认识的字段和取值', () => {
    expect(cleanPrefs({ hideOnFullscreen: false, sitBreakMinutes: 45, evil: 1 })).toEqual({
      ...DEFAULT_DESKTOP_PREFS,
      hideOnFullscreen: false,
      sitBreakMinutes: 45
    })
    expect(cleanPrefs({ sitBreakMinutes: 7, powerSaver: 'yes' })).toEqual(DEFAULT_DESKTOP_PREFS)
    expect(cleanPrefs(null)).toEqual(DEFAULT_DESKTOP_PREFS)
  })
})

describe('提醒本', () => {
  it('加、按时间排、到点拿走', () => {
    const book = new ReminderBook()
    const b = book.add(new Date(T0 + min(30)).toISOString(), ' 开会 ', T0, 'b')
    const a = book.add(new Date(T0 + min(10)).toISOString(), '喝水', T0, 'a')
    expect(typeof a).toBe('object')
    expect(typeof b).toBe('object')
    expect(book.list().map((r) => r.id)).toEqual(['a', 'b'])
    expect(book.list()[1].text).toBe('开会')
    expect(book.nextAt()).toBe(T0 + min(10))
    expect(book.takeDue(T0 + min(9), T0)).toEqual([])
    const due = book.takeDue(T0 + min(10), T0)
    expect(due.map((d) => [d.reminder.id, d.late])).toEqual([['a', false]])
    expect(book.list().map((r) => r.id)).toEqual(['b'])
  })

  it('时间不对、内容空的不收', () => {
    const book = new ReminderBook()
    expect(book.add(new Date(T0 - 1000).toISOString(), '过去', T0)).toBe('这个时间已经过了')
    expect(book.add(new Date(T0 + 40 * 24 * 3600_000).toISOString(), '太远', T0)).toContain('一个月')
    expect(book.add('明天', '看不懂', T0)).toBe('时间看不懂')
    expect(book.add(new Date(T0 + 1000).toISOString(), '   ', T0)).toContain('空')
    expect(book.list()).toEqual([])
  })

  it('程序没开时过了点的：12 小时内的补上并标出来，更早的丢掉', () => {
    const book = new ReminderBook([
      { id: 'old', at: new Date(T0 - LATE_WINDOW_MS - 1000).toISOString(), text: '昨天的', createdAt: '' },
      { id: 'missed', at: new Date(T0 - min(20)).toISOString(), text: '刚才的', createdAt: '' },
      { id: 'later', at: new Date(T0 + min(5)).toISOString(), text: '等会儿', createdAt: '' }
    ])
    const due = book.takeDue(T0, T0)
    expect(due.map((d) => [d.reminder.id, d.late])).toEqual([['missed', true]])
    expect(book.list().map((r) => r.id)).toEqual(['later'])
  })

  it('读进来的坏数据跳过', () => {
    const book = new ReminderBook([{ id: 1 } as never, { id: 'x', at: 'nope', text: 'a', createdAt: '' }])
    expect(book.list()).toEqual([])
  })
})

describe('久坐提醒', () => {
  it('连续用满 N 分钟提醒一次，之后重新计时', () => {
    const t = new SitTimer(45)
    expect(t.tick(T0, 0)).toBe(false) // 开始计时
    expect(t.tick(T0 + min(44), 10)).toBe(false)
    expect(t.tick(T0 + min(45), 10)).toBe(true)
    expect(t.tick(T0 + min(46), 10)).toBe(false)
    expect(t.tick(T0 + min(90), 10)).toBe(true)
  })

  it('中间离开过就从头算；关掉就不提醒', () => {
    const t = new SitTimer(30)
    t.tick(T0, 0)
    expect(t.tick(T0 + min(20), BREAK_IDLE_SECONDS)).toBe(false) // 离开了 5 分钟
    expect(t.tick(T0 + min(40), 0)).toBe(false) // 从 20 分钟那时重新算，才 20 分钟
    expect(t.tick(T0 + min(50), 0)).toBe(true)
    t.minutes = 0
    expect(t.tick(T0 + min(200), 0)).toBe(false)
  })
})
