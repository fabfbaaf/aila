/**
 * 提醒和久坐提醒：纯逻辑，时间都从外面传进来（测试里不用真的等）。
 *
 * - 提醒存在主进程（userData/reminders.json），AI 服务不存用户数据：对话里她只负责把「25 分钟后」算成具体时间
 * - 程序没开的时候到了点的提醒：12 小时内的，下次启动时补上（说「刚才……」）；更早的就算了
 * - 久坐：连续用电脑满 N 分钟提醒一次；中间离开（系统空闲）超过 5 分钟就从头算
 */

import { randomUUID } from 'crypto'
import type { Reminder } from '../shared/desktop'

/** 最多记多少条、最远提前多久、内容多长 */
export const MAX_REMINDERS = 50
export const MAX_AHEAD_MS = 31 * 24 * 3600 * 1000
export const MAX_TEXT = 200
/** 程序没开时错过的提醒，多久以内的启动时补上 */
export const LATE_WINDOW_MS = 12 * 3600 * 1000

export class ReminderBook {
  private items: Reminder[]

  constructor(items: Reminder[] = []) {
    this.items = items.filter(valid).sort(byTime)
  }

  list(): Reminder[] {
    return [...this.items]
  }

  /** 新加一条；时间不对（已经过了、太远）或者内容空的返回说明 */
  add(at: string, text: string, now: number, id: string = randomUUID()): Reminder | string {
    const when = Date.parse(at)
    const clean = text.trim().slice(0, MAX_TEXT)
    if (!clean) return '提醒的内容是空的'
    if (Number.isNaN(when)) return '时间看不懂'
    if (when <= now) return '这个时间已经过了'
    if (when - now > MAX_AHEAD_MS) return '太远了：最多提前一个月'
    if (this.items.length >= MAX_REMINDERS) return `提醒太多了（最多 ${MAX_REMINDERS} 条），先删掉几条`
    const r: Reminder = { id, at: new Date(when).toISOString(), text: clean, createdAt: new Date(now).toISOString() }
    this.items.push(r)
    this.items.sort(byTime)
    return r
  }

  remove(id: string): boolean {
    const before = this.items.length
    this.items = this.items.filter((r) => r.id !== id)
    return this.items.length !== before
  }

  /** 到点了的（按时间先后），同时从本子里拿掉；late：程序没开时就过了点 */
  takeDue(now: number, startedAt: number): { reminder: Reminder; late: boolean }[] {
    const due: { reminder: Reminder; late: boolean }[] = []
    const keep: Reminder[] = []
    for (const r of this.items) {
      const t = Date.parse(r.at)
      if (t > now) keep.push(r)
      else if (now - t <= LATE_WINDOW_MS) due.push({ reminder: r, late: t < startedAt })
      // 更早的：程序关了太久，提醒已经没意义了，丢掉
    }
    this.items = keep
    return due
  }

  /** 下一条什么时候到（没有就是 null） */
  nextAt(): number | null {
    return this.items.length ? Date.parse(this.items[0].at) : null
  }
}

function valid(r: unknown): r is Reminder {
  const x = r as Reminder
  return typeof x?.id === 'string' && typeof x.at === 'string' && !Number.isNaN(Date.parse(x.at)) && typeof x.text === 'string'
}

function byTime(a: Reminder, b: Reminder): number {
  return Date.parse(a.at) - Date.parse(b.at)
}

/** 离开电脑多久算休息过了（系统空闲秒数） */
export const BREAK_IDLE_SECONDS = 5 * 60

/** 久坐提醒：每分钟喂一次「现在几点、系统空闲了几秒」，该提醒时返回 true */
export class SitTimer {
  private since: number | null = null

  constructor(public minutes: number) {}

  tick(now: number, idleSeconds: number): boolean {
    if (this.minutes <= 0) {
      this.since = null
      return false
    }
    if (idleSeconds >= BREAK_IDLE_SECONDS || this.since === null) {
      // 刚离开过（或者刚开始算）：从现在重新计时
      this.since = now
      return false
    }
    if (now - this.since >= this.minutes * 60_000) {
      this.since = now
      return true
    }
    return false
  }
}
