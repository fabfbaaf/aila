/**
 * 每周回顾的材料（桌面端这边有的）：这周的编程任务、聊了几句、下周的提醒和日程。
 * AI 服务那边再加上记忆库里这周的经历（app/review.py）。纯函数，好测。
 */
import type { AgentSessionSummary } from '../shared/agent'
import type { StoredMemory, StoredMessage } from '../shared/cloud'
import type { Reminder } from '../shared/desktop'
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
}): ReviewBody {
  const w = weekOf(input.now)
  const inWeek = (d: Date): boolean => weekOf(d).week === w.week
  const tasks = input.sessions
    .filter((s) => !isStudioSession(s) && s.kind !== 'computer' && s.updated !== undefined && inWeek(new Date(s.updated * 1000)))
    .map((s) => ({
      title: s.title.slice(0, 200),
      project: s.cwd,
      // 做完：上一轮正常结束、没在等你、计划（有的话）都做完了
      done: s.outcome === 'done' && !s.waiting && !s.running && (!s.plan || s.plan.finished)
    }))
  const chats = input.messages.filter((m) => !m.deleted && m.role === 'user' && inWeek(new Date(m.createdAt))).length
  const soon = input.now.getTime() + 7 * 86_400_000
  const when = (d: Date): string => `${d.getMonth() + 1} 月 ${d.getDate()} 日 ${pad(d.getHours())}:${pad(d.getMinutes())}`
  const upcoming = [
    ...input.reminders
      .filter((r) => {
        const t = new Date(r.at).getTime()
        return t > input.now.getTime() && t <= soon
      })
      .map((r) => `${when(new Date(r.at))} 提醒：${r.text}`),
    ...input.memories
      .filter((m) => !m.deleted && m.kind === 'event' && m.expiresAt && new Date(m.expiresAt).getTime() > input.now.getTime())
      .filter((m) => new Date(m.expiresAt as string).getTime() <= soon + 86_400_000)
      .map((m) => m.content)
  ].slice(0, 30)
  return { ...w, tasks: tasks.slice(0, 100), chats, upcoming }
}
