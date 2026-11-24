/**
 * 「她知道你在忙什么」：每天第一次见面时说说昨天做了什么、今天有什么；晚上（可选）说说今天的日报。
 *
 * 材料都在本机：编程任务的会话（AI 服务的 /agent/sessions：标题、什么时候动过、在不在分支上、用量）、
 * 她记得的日程（记忆里 kind = event、今天到期的）、记下的提醒、今天聊了几句。不调大模型，话由这里拼出来：
 * 快、不花钱、说的数字都是真的。
 *
 * 只是纯函数（时间从外面传进来），什么时候说由 desktopKeeper 决定。
 */

import type { AgentSessionSummary } from '../shared/agent'
import type { StoredMemory, StoredMessage } from '../shared/cloud'
import type { Reminder } from '../shared/desktop'
import { occasionLine, type Work } from '../shared/studio'
import { mainLang, tm } from './lang'

/** 英文时句子之间要空格、引号和顿号换成英文的 */
const en = (): boolean => mainLang() === 'en'
const quote = (s: string): string => (en() ? `"${s}"` : `「${s}」`)
const sep = (): string => (en() ? ' ' : '')

export { REPORT_TIMES } from '../shared/desktop'

export interface BriefingInput {
  now: Date
  sessions: AgentSessionSummary[]
  memories: StoredMemory[]
  reminders: Reminder[]
  messages: StoredMessage[]
  /** 她自己的作品（昨天做好的早上提一句） */
  works?: Work[]
  /** 今天是不是特别的日子（认识第 N 天、节日） */
  occasion?: string
}

/** 她做作品用的会话（工作目录是 …/works/<作品 id>）：不算你的任务 */
export function isStudioSession(s: Pick<AgentSessionSummary, 'cwd'>): boolean {
  return /[\\/]works[\\/]\d{8}-\d{6}-[0-9a-f]{4}[\\/]?$/.test(s.cwd)
}

/** 早上几点以后才算「新的一天」（凌晨两点还在写代码的话，还算昨天） */
export const DAY_STARTS_AT = 5

/** 本地日期键：凌晨 5 点前算前一天 */
export function workday(d: Date): string {
  const shifted = new Date(d.getTime() - DAY_STARTS_AT * 3600 * 1000)
  return `${shifted.getFullYear()}-${String(shifted.getMonth() + 1).padStart(2, '0')}-${String(shifted.getDate()).padStart(2, '0')}`
}

function sameWorkday(ms: number | undefined, day: string): boolean {
  return ms !== undefined && workday(new Date(ms)) === day
}

function previousWorkday(now: Date): string {
  return workday(new Date(now.getTime() - 24 * 3600 * 1000))
}

/** 会话最后一次动的时间（毫秒）；summary 里 updated 是秒 */
function touched(s: AgentSessionSummary): number | undefined {
  return s.updated === undefined ? undefined : s.updated * 1000
}

function titles(list: AgentSessionSummary[], max = 2): string {
  const shown = list.slice(0, max).map((s) => quote(`${s.title.slice(0, 16)}${s.title.length > 16 ? '…' : ''}`))
  const joined = shown.join(en() ? ', ' : '、')
  return list.length > max ? tm('{list} 等 {n} 个', { list: joined, n: list.length }) : joined
}

/** 记忆里的「用户……」换成「你……」，读起来像她在跟你说 */
function asYou(text: string): string {
  return text.replace(/^用户/, tm('你')).replace(/。$/, '')
}

/** 今天的日程：记忆里有时效的事，今天到期（今天就是那件事的日子） */
function todaysEvents(memories: StoredMemory[], now: Date): StoredMemory[] {
  const day = workday(now)
  return memories.filter((m) => !m.deleted && m.kind === 'event' && m.expiresAt && workday(new Date(m.expiresAt)) === day)
}

/** 某一天（工作日键）的日程 */
function eventsOn(memories: StoredMemory[], day: string): StoredMemory[] {
  return memories.filter((m) => !m.deleted && m.kind === 'event' && m.expiresAt && workday(new Date(m.expiresAt)) === day)
}

/** 往后（正数）或往前（负数）数几天的工作日键 */
function shiftDay(now: Date, days: number): string {
  return workday(new Date(now.getTime() + days * 24 * 3600 * 1000))
}

/** 日程的那件事本身（「用户周五要面试」→「面试」这种就不拆了，原样用，只把「用户」换成「你」） */
function eventText(m: StoredMemory): string {
  return asYou(m.content).slice(0, 30)
}

function greet(hour: number): string {
  if (hour >= 5 && hour < 11) return tm('早上好。')
  if (hour >= 11 && hour < 14) return tm('中午好。')
  if (hour < 5) return tm('这么晚了……')
  return tm('你来啦。')
}

/**
 * 每天第一次见面说的话。没什么可说（昨天没做任务、今天没日程没提醒）就只打招呼，返回的 detail 为 false。
 * 最多三句：昨天的任务、还没合并的分支、今天的事。
 */
export function morningBriefing(input: BriefingInput, withGreeting = true): { text: string; detail: boolean } {
  const { now } = input
  const yesterday = previousWorkday(now)
  const today = workday(now)
  const parts: string[] = []

  const sessions = input.sessions.filter((s) => !isStudioSession(s))
  const occasion = occasionLine(input.occasion ?? '')
  if (occasion) parts.push(occasion)

  const done = sessions.filter((s) => sameWorkday(touched(s), yesterday))
  if (done.length) parts.push(tm('昨天一起做了 {n} 个任务：{list}。', { n: done.length, list: titles(done) }))

  const open = sessions.filter((s) => s.branch && !sameWorkday(touched(s), today))
  if (open.length) parts.push(tm('{list}还在分支上，没合并。', { list: titles(open, 1) }))

  // 她自己的作品：昨天（或今天一早）做好的
  const made = (input.works ?? []).filter((w) => w.status === 'done' && [yesterday, today].includes(workday(new Date(w.created * 1000))))
  if (made.length) parts.push(tm('昨天我自己做了个小东西「{title}」，放在「作品」里了。', { title: made[0].title }))

  const events = todaysEvents(input.memories, now)
  const reminders = input.reminders.filter((r) => workday(new Date(r.at)) === today)
  const todayBits = [
    ...events.slice(0, 2).map((m) => asYou(m.content)),
    ...(reminders.length ? [tm('{n} 个提醒', { n: reminders.length })] : [])
  ]
  if (todayBits.length) parts.push(tm('今天：{what}。', { what: todayBits.join(en() ? '; ' : '；') }))

  // 记得你的事：明天的日程提前说一声；前一两天的事问问后来怎么样
  const tomorrow = eventsOn(input.memories, shiftDay(now, 1))
  if (tomorrow.length) parts.push(tm('明天：{what}……准备得怎么样？', { what: eventText(tomorrow[0]) }))
  const past = [...eventsOn(input.memories, shiftDay(now, -1)), ...eventsOn(input.memories, shiftDay(now, -2))]
  if (past.length) parts.push(tm('之前说的「{what}」，后来怎么样了？', { what: eventText(past[0]) }))

  // 程序刚启动时她已经打过招呼了（Behavior.greet），这时就不再说「早上好」
  return { text: [withGreeting ? greet(now.getHours()) : '', ...parts].filter(Boolean).join(sep()), detail: parts.length > 0 }
}

/** 晚上的日报：今天做了几个任务、几个还在分支上、用了多少 token、命中多少缓存、聊了几句 */
export function dailyReport(input: BriefingInput): string {
  const today = workday(input.now)
  const tasks = input.sessions.filter((s) => !isStudioSession(s) && sameWorkday(touched(s), today))
  const chats = input.messages.filter((m) => !m.deleted && m.role === 'user' && workday(new Date(m.createdAt)) === today).length
  if (!tasks.length && !chats) return tm('今天还没一起做什么……明天见。')

  const parts: string[] = []
  if (tasks.length) {
    const prompt = tasks.reduce((n, s) => n + (s.usage?.prompt ?? 0), 0)
    const hit = tasks.reduce((n, s) => n + (s.usage?.cache_hit ?? 0), 0)
    const completion = tasks.reduce((n, s) => n + (s.usage?.completion ?? 0), 0)
    const branches = tasks.filter((s) => s.branch).length
    parts.push(branches
      ? tm('今天一起做了 {n} 个任务（{b} 个还在分支上没合并）。', { n: tasks.length, b: branches })
      : tm('今天一起做了 {n} 个任务。', { n: tasks.length }))
    if (prompt + completion > 0) {
      parts.push(prompt
        ? tm('用了 {t}个 token，输入里 {pct}% 命中了缓存。', { t: tokens(prompt + completion), pct: Math.round((hit / prompt) * 100) })
        : tm('用了 {t}个 token。', { t: tokens(prompt + completion) }))
    }
  }
  if (chats) parts.push(tm('和我聊了 {n} 句。', { n: chats }))
  return [...parts, tm('……今天辛苦了。')].join(sep())
}

function tokens(n: number): string {
  if (n >= 10_000) return en() ? `${Math.round(n / 1000)}k ` : `${(n / 10_000).toFixed(n >= 100_000 ? 0 : 1)} 万`
  return `${n} `
}

/** 现在该不该说：今天还没说过、已经过了该说的时间 */
export function due(lastDay: string | null | undefined, now: Date, at = ''): boolean {
  if (lastDay === workday(now)) return false
  if (!at) return true
  const [h, m] = at.split(':').map(Number)
  const minutes = now.getHours() * 60 + now.getMinutes()
  // 过了点才说；凌晨（新的一天还没开始）不补昨天的日报
  return now.getHours() >= DAY_STARTS_AT && minutes >= h * 60 + m
}
