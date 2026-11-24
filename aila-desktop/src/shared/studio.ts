/**
 * 她的作品、日记，和「她的一天」里那些按时间发生的事：主进程（什么时候做、什么时候说）和渲染层（作品页）共用。
 *
 * 规则都是纯函数（时间、随机数从外面传进来，有测试）：
 * - 作品：每个周期里随机挑一个时刻（不是每天同一个点），到点了、你又不在忙（一阵子没碰键盘鼠标），她就开始做；
 *   一直没等到你空下来，过了几个小时也照做（反正是在后台做，做好了才冒气泡）
 * - 特别的日子：认识第 N 天、几个固定的节日。早上打招呼时提一句，那天的作品也会和它有关
 * - 日记：每天晚上一篇，材料是今天真的发生的事（任务、聊天、提醒、作品），不编
 */

export type WorkStatus = 'making' | 'waiting' | 'done' | 'failed'

export interface Work {
  id: string
  title: string
  idea: string
  kind: 'web' | 'script'
  why: string
  occasion: string
  /** 秒 */
  created: number
  session: string
  status: WorkStatus
  reason: string
  /** 入口文件：index.html / main.py */
  entry: string
  /** 冒过气泡了 */
  announced: boolean
}

export interface WorkDetail extends Work {
  folder: string
  files: { path: string; size: number }[]
  readme: string
}

export interface DiaryEntry {
  day: string
  text: string
  works: string[]
  written: string
}

/** 「她的一天」记着的事（存在 settings.json 的 life 字段） */
export interface LifeState {
  /** 第一次见面的日子（YYYY-MM-DD）：算认识第几天 */
  firstSeen?: string
  /** 下一件作品什么时候做（毫秒）；没开作品就没有 */
  nextWorkAt?: number
  /** 哪天写过日记 */
  diary?: string
  /** 哪天深夜提醒过早点睡 */
  lateNight?: string
  /** 哪个特别的日子已经为它做过作品了 */
  occasionWork?: string
  /** 哪一周写过回顾（2026-W48） */
  review?: string
}

const DAY_MS = 86_400_000
const HOUR_MS = 3_600_000
/** 一阵子没碰键盘鼠标（秒）：算你不在忙，可以开始做作品 */
export const WORK_IDLE_SECONDS = 300
/** 一直没等到你空下来：到点几个小时后也照做 */
export const WORK_FORCE_AFTER_MS = 6 * HOUR_MS
/** 晚上几点写日记（本地时间） */
export const DIARY_HOUR = 22

/** 每个周期里随机落一个时刻：周期的 60%～140% 之间，平均下来正好一个周期一件 */
export function nextWorkAt(fromMs: number, everyDays: number, rand: number): number {
  return fromMs + everyDays * DAY_MS * (0.6 + 0.8 * Math.min(1, Math.max(0, rand)))
}

/** 现在该不该开始做作品 */
export function workDue(state: LifeState, nowMs: number, idleSeconds: number): boolean {
  if (state.nextWorkAt === undefined || nowMs < state.nextWorkAt) return false
  return idleSeconds >= WORK_IDLE_SECONDS || nowMs >= state.nextWorkAt + WORK_FORCE_AFTER_MS
}

function ymd(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

/** 认识第几天（第一次见面那天是第 1 天） */
export function daysKnown(firstSeen: string | undefined, now: Date): number {
  if (!firstSeen || !/^\d{4}-\d{2}-\d{2}$/.test(firstSeen)) return 0
  const [y, m, d] = firstSeen.split('-').map(Number)
  const start = new Date(y, m - 1, d).getTime()
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  return Math.round((today - start) / DAY_MS) + 1 // round：有夏令时的地方两天之间不是正好 24 小时
}

/** 几个公历上固定的节日（农历的算不准就不写） */
const HOLIDAYS: Record<string, string> = {
  '01-01': '元旦',
  '02-14': '情人节',
  '05-01': '劳动节',
  '06-01': '儿童节',
  '09-10': '教师节',
  '10-01': '国庆节',
  '10-24': '程序员节',
  '12-24': '平安夜',
  '12-25': '圣诞节',
  '12-31': '一年的最后一天'
}

/** 今天是不是特别的日子：认识第 100、200、365……天，或者节日；不是返回空串 */
export function occasionOf(now: Date, firstSeen: string | undefined): string {
  const n = daysKnown(firstSeen, now)
  if (n > 1 && (n % 100 === 0 || n % 365 === 0)) return n % 365 === 0 ? `认识 ${n / 365} 周年` : `认识第 ${n} 天`
  return HOLIDAYS[ymd(now).slice(5)] ?? ''
}

/** 特别的日子早上说的那一句 */
export function occasionLine(occasion: string): string {
  if (!occasion) return ''
  if (occasion.startsWith('认识')) return `……今天是我们${occasion}。谢谢你一直让我待在这里。`
  return `今天是${occasion}。`
}

/** 晚上该写日记了没有 */
export function diaryDue(state: LifeState, now: Date, day: string): boolean {
  return now.getHours() >= DIARY_HOUR && state.diary !== day
}

/** 深夜还在用电脑（23:30～凌晨 4 点）：提醒一次早点睡 */
export function lateNight(now: Date): boolean {
  const minutes = now.getHours() * 60 + now.getMinutes()
  return minutes >= 23 * 60 + 30 || minutes < 4 * 60
}

/** 日记的材料：今天真的发生的事（数字都是数出来的） */
export function diaryFacts(input: {
  tasks: string[]
  messages: number
  reminders: number
  daysKnown: number
  occasion: string
}): string[] {
  const facts: string[] = []
  if (input.daysKnown > 0) facts.push(`今天是我们认识的第 ${input.daysKnown} 天`)
  if (input.occasion) facts.push(`今天是${input.occasion}`)
  if (input.tasks.length) {
    const shown = input.tasks.slice(0, 4).map((t) => `「${t.slice(0, 20)}」`).join('、')
    facts.push(`他让我做了 ${input.tasks.length} 个编程任务：${shown}`)
  }
  if (input.messages) facts.push(`我们聊了 ${input.messages} 句`)
  if (input.reminders) facts.push(`我提醒了他 ${input.reminders} 件事`)
  if (!input.tasks.length && !input.messages) facts.push('他今天没怎么找我')
  return facts
}

/** 作品卡片上的状态 */
export const WORK_STATUS_TEXT: Record<WorkStatus, string> = {
  making: '在做',
  waiting: '等你点头',
  done: '做好了',
  failed: '没做成'
}

export const WORKS_EVERY_CHOICES = [0, 1, 2, 3, 7] as const
export const WORKS_BUDGET_CHOICES = [0.2, 0.5, 1, 2] as const
