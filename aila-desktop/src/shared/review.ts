/**
 * 每周回顾（AI 服务的 app/review.py 写，作品页的「回顾」里看）：哪一周、什么时候该写。
 * 周一到周日算一周（ISO 周），周五晚上六点以后、周末都可以写；一周只自动写一次。
 */

export interface ReviewEntry {
  /** 2026-W48 */
  week: string
  start: string
  end: string
  markdown: string
  written: string
}

/** 自动写的时间：周五 18 点以后 */
export const REVIEW_HOUR = 18

const pad = (n: number): string => String(n).padStart(2, '0')
const ymd = (d: Date): string => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`

/** 这一天在哪一周：周号（ISO，周一开始）、这周一、这周日（本地日期） */
export function weekOf(d: Date): { week: string; start: string; end: string } {
  const day = (d.getDay() + 6) % 7 // 周一 0 … 周日 6
  const monday = new Date(d.getFullYear(), d.getMonth(), d.getDate() - day)
  const sunday = new Date(monday.getFullYear(), monday.getMonth(), monday.getDate() + 6)
  // ISO 周号：这周四在哪一年、是那年的第几个周四
  const thursday = new Date(monday.getFullYear(), monday.getMonth(), monday.getDate() + 3)
  const jan1 = new Date(thursday.getFullYear(), 0, 1)
  const n = Math.floor((thursday.getTime() - jan1.getTime()) / 86_400_000 / 7) + 1
  return { week: `${thursday.getFullYear()}-W${pad(n)}`, start: ymd(monday), end: ymd(sunday) }
}

/** 该不该自动写：周五 18 点以后或者周末，而且这周还没写过 */
export function reviewDue(lastWeek: string | undefined, now: Date): boolean {
  const day = now.getDay() // 周日 0，周五 5，周六 6
  const late = (day === 5 && now.getHours() >= REVIEW_HOUR) || day === 6 || day === 0
  return late && lastWeek !== weekOf(now).week
}
