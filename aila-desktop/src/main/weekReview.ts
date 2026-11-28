/**
 * 每周回顾的材料（桌面端这边有的）：这周的编程任务、聊了几句、下周的提醒和日程。
 * AI 服务那边再加上记忆库里这周的经历（app/review.py）。纯函数，好测。
 */
import type { AgentSessionSummary } from '../shared/agent'
import type { StoredMemory, StoredMessage } from '../shared/cloud'
import type { Reminder } from '../shared/desktop'
import type { Lang } from '../shared/i18n'
import { weekOf } from '../shared/review'
import { isStudioSession } from './briefing'

export interface ReviewBody {
  week: string
  start: string
  end: string
  tasks: { title: string; project: string; done: boolean }[]
  chats: number
  upcoming: string[]
  force?: boolean
}

const pad = (n: number): string => String(n).padStart(2, '0')

export function reviewBody(input: {
  sessions: AgentSessionSummary[]
  messages: StoredMessage[]
  reminders: Reminder[]
  memories: StoredMemory[]
  now: Date
  lang?: Lang
}): ReviewBody {
  const now = input.now
  const w = weekOf(now)
  const nextWeek = weekOf(new Date(now.getFullYear(), now.getMonth(), now.getDate() + 7)).week
  const isWeek = (date: Date, week: string): boolean => Number.isFinite(date.getTime()) && weekOf(date).week === week
  const tasks = input.sessions
    .filter((s) => !isStudioSession(s) && s.kind !== 'computer' && s.updated !== undefined && isWeek(new Date(s.updated * 1000), w.week))
    .map((s) => ({
      title: s.title.slice(0, 200),
      project: s.cwd,
      // 做完：上一轮正常结束、没在等你、计划（有的话）都做完了
      done: s.outcome === 'done' && !s.waiting && !s.running && (!s.plan || s.plan.finished)
    }))
  const chats = input.messages.filter((m) => !m.deleted && m.role === 'user' && isWeek(new Date(m.createdAt), w.week)).length
  const english = input.lang === 'en'
  const when = (date: Date): string => {
    const day = english
      ? new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric' }).format(date)
      : `${date.getMonth() + 1} 月 ${date.getDate()} 日`
    return `${day} ${pad(date.getHours())}:${pad(date.getMinutes())}`
  }
  const upcoming = [
    ...input.reminders
      .filter((r) => isWeek(new Date(r.at), nextWeek))
      .map((r) => `${when(new Date(r.at))} ${english ? 'Reminder: ' : '提醒：'}${r.text}`),
    ...input.memories
      .filter((m) => !m.deleted && m.kind === 'event' && m.expiresAt && isWeek(new Date(m.expiresAt), nextWeek))
      .map((m) => m.content)
  ].slice(0, 30)
  return { ...w, tasks: tasks.slice(0, 100), chats, upcoming }
}
