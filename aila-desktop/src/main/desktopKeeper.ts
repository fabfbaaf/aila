/**
 * 桌面上的她：什么时候躲开、省不省电、提醒什么时候响。主进程里的一个管家，判断规则都在纯函数里
 * （presence.ts、reminders.ts，有测试），这里只负责接系统事件、存文件、显示 / 藏起窗口、发通知。
 *
 * - 状态（偏好、在不在、拔没拔电、提醒列表）变了就整份推给所有窗口（desktop:status），渲染层自己决定帧率、说不说话
 * - 提醒到点：她在桌面上而且能说话 → 让她说；藏起来了（全屏、免打扰）→ 系统通知，开会时也不会漏
 * - 偏好存在 userData/settings.json 的 desktop 字段；提醒存在 userData/reminders.json
 */

import { app, Notification, powerMonitor, type BrowserWindow } from 'electron'
import { existsSync, readFileSync, renameSync, writeFileSync } from 'fs'
import { join } from 'path'
import { cleanPrefs, type DesktopPrefs, type DesktopStatus, type FocusState, type Presence, type Reminder } from '../shared/desktop'
import type { AiResult, CharacterEvent } from '../shared/settings'
import {
  daysKnown,
  diaryDue,
  diaryFacts,
  lateNight,
  nextWorkAt,
  occasionOf,
  workDue,
  type LifeState,
  type Work
} from '../shared/studio'
import { dailyReport, due, isStudioSession, morningBriefing, workday, type BriefingInput } from './briefing'
import type { StartWorkBody } from './studio'
import { decidePresence, FocusWatch } from './presence'
import { ReminderBook, SitTimer } from './reminders'
import { reviewDue, type ReviewEntry } from '../shared/review'
import { reviewBody, type ReviewBody } from './weekReview'
import { tm } from './lang'

/** 最长隔多久重新排一次闹钟（电脑睡眠后 setTimeout 会不准） */
const MAX_TIMER_MS = 60 * 60 * 1000
/** 程序启动后多久内算「刚启动」：她已经打过招呼，每天见面的话不再说早上好 */
const FRESH_MS = 2 * 60 * 1000
/** 系统空闲超过这么久就当人不在电脑前：先不说，回来再说 */
const AWAY_SECONDS = 120

/** 今天说过没有（存在 settings.json 的 briefing 字段） */
export interface BriefingState {
  morning?: string
  evening?: string
}

export interface DesktopKeeperOptions {
  dataDir: string
  character: () => BrowserWindow | null
  characterEvent: (ev: CharacterEvent) => void
  onChange: (status: DesktopStatus) => void
  loadPrefs: () => unknown
  savePrefs: (prefs: DesktopPrefs) => void
  /** 每天见面、日报要用的材料；AI 服务没开时返回 null（过一分钟再试） */
  gather: () => Promise<Omit<BriefingInput, 'now' | 'reminders'> | null>
  loadBriefing: () => BriefingState
  saveBriefing: (state: BriefingState) => void
  /** 她的一天：作品、日记（AI 服务的接口）和记着的状态。不给就没有这些 */
  life?: LifeHooks
}

export interface LifeHooks {
  load: () => LifeState
  save: (state: LifeState) => void
  works: () => Promise<Work[] | null>
  start: (body: StartWorkBody) => Promise<AiResult<Work>>
  seen: (id: string) => Promise<void>
  writeDiary: (day: string, facts: string[]) => Promise<boolean>
  /** 写这周的回顾（app/review.py） */
  writeReview: (body: ReviewBody) => Promise<AiResult<ReviewEntry>>
}

/** 本地日期（YYYY-MM-DD），不按 5 点切 */
function ymd(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

/** 挑作品点子的参考：她记得的你的事、最近的任务（不算她自己做作品的会话） */
function workHints(
  got: { sessions: { cwd: string; title: string }[]; memories: { deleted?: boolean; content: string }[] },
  occasion: string
): { memories: string[]; tasks: string[]; occasion: string } {
  return {
    memories: got.memories.filter((m) => !m.deleted).map((m) => m.content).slice(0, 12),
    tasks: got.sessions.filter((s) => !isStudioSession(s)).map((s) => s.title).slice(0, 8),
    occasion
  }
}

export class DesktopKeeper {
  prefs: DesktopPrefs
  dnd = false
  /** 不显示她的时候临时叫出来说话 */
  private summoned = false
  presence: Presence = { visible: true, quiet: false, reason: 'normal' }
  private focus: FocusState = 'normal'
  private book: ReminderBook
  private sit: SitTimer
  private readonly startedAt = Date.now()
  private alarm: NodeJS.Timeout | undefined
  private minute: NodeJS.Timeout | undefined
  private briefing: BriefingState
  private briefingBusy = false
  private life: LifeState
  private lifeBusy = false
  /** 上次告诉桌面上的她「在做作品」没有 */
  private making = false
  private watch = new FocusWatch((f) => {
    this.focus = f
    this.applyPresence()
  })

  constructor(private o: DesktopKeeperOptions) {
    this.prefs = cleanPrefs(o.loadPrefs())
    this.book = new ReminderBook(this.readReminders())
    this.sit = new SitTimer(this.prefs.sitBreakMinutes)
    this.briefing = o.loadBriefing() ?? {}
    this.life = o.life?.load() ?? {}
    // 一开始就按偏好算好在不在：关了「桌面上显示她」的话，角色窗口第一次显示前就知道别显示（不会闪一下）
    this.presence = decidePresence(this.focus, this.dnd, this.prefs)
  }

  get file(): string {
    return join(this.o.dataDir, 'reminders.json')
  }

  get loginSupported(): boolean {
    return app.isPackaged
  }

  start(): void {
    this.watch.start()
    const changed = (): void => this.publish()
    powerMonitor.on('on-battery', changed)
    powerMonitor.on('on-ac', changed)
    // 睡眠醒来、解锁：闹钟可能错过了，马上看一遍；人回来了，过几秒看看今天见过面没有
    const back = (): void => {
      this.ring()
      setTimeout(() => void this.everyMinute(), 5_000)
    }
    powerMonitor.on('resume', back)
    powerMonitor.on('unlock-screen', back)
    this.minute = setInterval(() => void this.everyMinute(), 60_000)
    // 启动后先等 AI 服务起来（05 会自己开它），再看今天见过面没有
    setTimeout(() => void this.everyMinute(), 20_000)
    this.applyLogin()
    this.ring() // 程序没开时过了点的，现在补上
  }

  stop(): void {
    this.watch.stop()
    clearTimeout(this.alarm)
    clearInterval(this.minute)
  }

  status(): DesktopStatus {
    return {
      prefs: { ...this.prefs },
      presence: { ...this.presence },
      dnd: this.dnd,
      onBattery: powerMonitor.isOnBatteryPower(),
      loginSupported: this.loginSupported,
      reminders: this.book.list()
    }
  }

  setPrefs(raw: unknown): DesktopStatus {
    const before = this.prefs
    this.prefs = cleanPrefs(raw, before)
    this.sit.minutes = this.prefs.sitBreakMinutes
    this.o.savePrefs(this.prefs)
    if (before.openAtLogin !== this.prefs.openAtLogin) this.applyLogin()
    this.applyPresence(true)
    return this.status()
  }

  setDnd(on: boolean): void {
    if (this.dnd === on) return
    this.dnd = on
    this.applyPresence(true)
  }

  /** 关了「桌面上显示她」时按「和她说话」：临时叫出来；对话框关了 summon(false) 藏回去 */
  summon(on: boolean): void {
    if (this.summoned === on) return
    this.summoned = on
    this.applyPresence(true)
  }

  // ---------- 提醒 ----------

  addReminder(at: string, text: string): Reminder | string {
    const r = this.book.add(at, text, Date.now())
    if (typeof r === 'string') return r
    this.saveReminders()
    this.ring()
    this.publish()
    return r
  }

  removeReminder(id: string): boolean {
    const ok = this.book.remove(id)
    if (ok) {
      this.saveReminders()
      this.ring()
      this.publish()
    }
    return ok
  }

  /** 「过 10 分钟再提醒」：同样的内容，过几分钟再来一次 */
  snooze(text: string, minutes: number): Reminder | string {
    return this.addReminder(new Date(Date.now() + minutes * 60_000).toISOString(), text)
  }

  /** 到点的都响一遍，再排下一次闹钟 */
  private ring(): void {
    clearTimeout(this.alarm)
    const due = this.book.takeDue(Date.now(), this.startedAt)
    if (due.length) {
      this.saveReminders()
      for (const d of due) this.deliver(d.reminder, d.late)
      this.publish()
    }
    const next = this.book.nextAt()
    if (next !== null) this.alarm = setTimeout(() => this.ring(), Math.min(Math.max(next - Date.now(), 0), MAX_TIMER_MS))
  }

  private deliver(reminder: Reminder, late: boolean): void {
    if (this.presence.visible && !this.presence.quiet) {
      this.o.characterEvent({ kind: 'reminder', reminder, late })
      return
    }
    // 她藏起来了（全屏、演示、免打扰）或者不方便出声：用系统通知，不会漏
    if (Notification.isSupported()) {
      const when = new Date(reminder.at).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
      new Notification({ title: late ? tm('艾拉 · 刚才 {when} 的提醒', { when }) : tm('艾拉 · 提醒'), body: reminder.text }).show()
    }
  }

  private async everyMinute(): Promise<void> {
    const idle = powerMonitor.getSystemIdleTime()
    const canTalk = this.presence.visible && !this.presence.quiet
    // 全屏、演示、免打扰时不打扰（打游戏、开会时不需要她来催）
    if (this.sit.tick(Date.now(), idle) && canTalk) {
      this.o.characterEvent({ kind: 'sit-break', minutes: this.prefs.sitBreakMinutes })
    }
    if (!this.lifeBusy) {
      this.lifeBusy = true
      try {
        await this.lifeTick(idle, canTalk)
      } catch {
        // AI 服务没开、网络抖动：下一分钟再说
      } finally {
        this.lifeBusy = false
      }
    }
    // 每天第一次见面、晚上的日报：人在电脑前、她能说话时才说；没说成的下一分钟再试
    if (!canTalk || idle >= AWAY_SECONDS || this.briefingBusy) return
    const now = new Date()
    if (due(this.briefing.morning, now)) await this.brief('morning', now)
    else if (this.prefs.reportAt && due(this.briefing.evening, now, this.prefs.reportAt)) await this.brief('evening', now)
  }

  /** 右键菜单「今天做了什么」：马上说今天的日报（不算晚上那一次） */
  async reportNow(): Promise<boolean> {
    return this.brief('evening', new Date(), false)
  }

  private async brief(when: 'morning' | 'evening', now: Date, record = true): Promise<boolean> {
    this.briefingBusy = true
    try {
      const got = await this.o.gather()
      if (!got) return false
      const input: BriefingInput = { ...got, now, reminders: this.book.list(), ...(await this.lifeForBriefing()) }
      if (record) {
        this.briefing = { ...this.briefing, [when]: workday(now) }
        this.o.saveBriefing(this.briefing)
      }
      if (when === 'evening') {
        this.o.characterEvent({ kind: 'briefing', when, text: dailyReport(input), detail: true })
        return true
      }
      const fresh = Date.now() - this.startedAt < FRESH_MS
      const m = morningBriefing(input, !fresh)
      // 刚启动、又没什么可说的：她已经打过招呼了，不用再说一遍
      if (fresh && !m.detail) return true
      this.o.characterEvent({ kind: 'briefing', when, text: m.text, detail: m.detail })
      return true
    } finally {
      this.briefingBusy = false
    }
  }

  // ---------- 她的一天：作品、日记、深夜 ----------

  private saveLife(): void {
    this.o.life?.save(this.life)
  }

  private async lifeTick(idle: number, canTalk: boolean): Promise<void> {
    const l = this.o.life
    if (!l) return
    const now = new Date()
    const ms = now.getTime()
    const day = workday(now)
    if (!this.life.firstSeen) {
      this.life.firstSeen = ymd(now)
      this.saveLife()
    }
    const occasion = occasionOf(now, this.life.firstSeen)

    // 作品：每个周期里随机挑个时刻；特别的日子当天也做一件；你不在忙的时候开始
    if (this.prefs.worksEvery > 0) {
      if (this.life.nextWorkAt === undefined) {
        this.life.nextWorkAt = nextWorkAt(ms, this.prefs.worksEvery, Math.random())
        this.saveLife()
      }
      if (occasion && this.life.occasionWork !== day) {
        this.life.nextWorkAt = Math.min(this.life.nextWorkAt, ms)
        this.life.occasionWork = day
        this.saveLife()
      }
      if (workDue(this.life, ms, idle)) await this.startWork(occasion)
    } else if (this.life.nextWorkAt !== undefined) {
      delete this.life.nextWorkAt
      this.saveLife()
    }

    // 在做的、做好了的：桌面上的她敲键盘；做好了冒个气泡（你在电脑前、她能说话的时候）
    const works = await l.works()
    if (works) {
      const making = works.find((w) => w.status === 'making' || w.status === 'waiting')
      if (!!making !== this.making) {
        this.making = !!making
        this.o.characterEvent({ kind: 'work-making', on: this.making, title: making?.title ?? '' })
      }
      const fresh = works.find((w) => w.status === 'done' && !w.announced)
      if (fresh && canTalk && idle < AWAY_SECONDS) {
        await l.seen(fresh.id)
        this.o.characterEvent({ kind: 'work', work: { id: fresh.id, title: fresh.title, why: fresh.why } })
      }
    }

    // 日记：晚上写一篇（不用你在电脑前）
    if (this.prefs.diary && diaryDue(this.life, now, day)) {
      const got = await this.o.gather()
      if (got) {
        const tasks = got.sessions
          .filter((s) => !isStudioSession(s) && s.updated !== undefined && workday(new Date(s.updated * 1000)) === day)
          .map((s) => s.title)
        const messages = got.messages.filter((m) => !m.deleted && m.role === 'user' && workday(new Date(m.createdAt)) === day).length
        const facts = diaryFacts({ tasks, messages, reminders: 0, daysKnown: daysKnown(this.life.firstSeen, now), occasion })
        if (await l.writeDiary(day, facts)) {
          this.life.diary = day
          this.saveLife()
        }
      }
    }

    // 每周回顾：周五晚上、周末写这周的（不用你在电脑前）；写好了她说一声
    if (this.prefs.weeklyReview && reviewDue(this.life.review, now)) {
      const r = await this.reviewNow(false)
      if (r.ok && r.data) {
        this.life.review = r.data.week
        this.saveLife()
        if (canTalk && idle < AWAY_SECONDS) this.o.characterEvent({ kind: 'review', week: r.data.week })
      }
    }

    // 深夜你还在用电脑：说一次早点睡
    if (canTalk && idle < 60 && lateNight(now) && this.life.lateNight !== day) {
      this.life.lateNight = day
      this.saveLife()
      this.o.characterEvent({ kind: 'late-night' })
    }
  }

  /** 开始做一件作品：带上她对你的了解（记忆、最近的任务）和今天的日子 */
  private async startWork(occasion: string): Promise<void> {
    const l = this.o.life
    const got = await this.o.gather()
    if (!l || !got) return
    const r = await l.start({ hints: workHints(got, occasion), budget: this.prefs.worksBudget })
    const ms = Date.now()
    // 做上了：排下一件；手上还有没做完的：一小时后再看；别的错误（服务没开、出错）：十分钟后再试
    if (r.ok) this.life.nextWorkAt = nextWorkAt(ms, this.prefs.worksEvery, Math.random())
    else this.life.nextWorkAt = ms + (['还没做完', tm('还没做完')].some((k) => (r.error ?? '').includes(k)) ? 3_600_000 : 600_000) // AI 服务的话可能是中文也可能翻好了
    this.saveLife()
  }

  /** 作品页里「现在就让她做一件」（可以带你给的点子） */
  async workNow(idea?: StartWorkBody['idea']): Promise<AiResult<Work>> {
    const l = this.o.life
    if (!l) return { ok: false, error: tm('还没准备好') }
    const got = await this.o.gather()
    const occasion = occasionOf(new Date(), this.life.firstSeen)
    return l.start({ hints: got ? workHints(got, occasion) : undefined, idea, budget: this.prefs.worksBudget })
  }

  /** 写这周的回顾。force：这周写过了也重写（作品页里点「重写」） */
  async reviewNow(force: boolean): Promise<AiResult<ReviewEntry>> {
    const l = this.o.life
    if (!l) return { ok: false, error: tm('还没准备好') }
    const got = await this.o.gather()
    if (!got) return { ok: false, error: tm('AI 服务没开，过会儿再试。') }
    const body = reviewBody({ sessions: got.sessions, messages: got.messages, memories: got.memories, reminders: this.book.list(), now: new Date() })
    return l.writeReview({ ...body, force })
  }

  /** 每天见面要用：她自己的作品、今天是什么日子 */
  async lifeForBriefing(): Promise<{ works: Work[]; occasion: string }> {
    const works = (await this.o.life?.works().catch(() => null)) ?? []
    return { works, occasion: occasionOf(new Date(), this.life.firstSeen) }
  }

  /** 认识第几天、第一次见面是哪天（作品页上显示） */
  knownSince(): { firstSeen: string | undefined; days: number } {
    return { firstSeen: this.life.firstSeen, days: daysKnown(this.life.firstSeen, new Date()) }
  }

  // ---------- 在不在、省电、开机启动 ----------

  private applyPresence(force = false): void {
    const next = decidePresence(this.focus, this.dnd, this.prefs, this.summoned)
    const same = next.visible === this.presence.visible && next.quiet === this.presence.quiet && next.reason === this.presence.reason
    this.presence = next
    const w = this.o.character()
    if (w && !w.isDestroyed()) {
      if (next.visible && !w.isVisible()) w.showInactive()
      else if (!next.visible && w.isVisible()) w.hide()
    }
    if (!same || force) this.publish()
  }

  private applyLogin(): void {
    // 开发时注册的是裸的 electron.exe，开机会弹一个空的 Electron：只在打包装好的版本里设
    if (!this.loginSupported) return
    app.setLoginItemSettings({ openAtLogin: this.prefs.openAtLogin })
  }

  private publish(): void {
    this.o.onChange(this.status())
  }

  // ---------- 文件 ----------

  private readReminders(): Reminder[] {
    try {
      if (!existsSync(this.file)) return []
      const data = JSON.parse(readFileSync(this.file, 'utf-8')) as unknown
      return Array.isArray(data) ? (data as Reminder[]) : []
    } catch {
      return []
    }
  }

  private saveReminders(): void {
    const tmp = this.file + '.tmp'
    writeFileSync(tmp, JSON.stringify(this.book.list(), null, 2), 'utf-8')
    renameSync(tmp, this.file)
  }
}
