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
import type { CharacterEvent } from '../shared/settings'
import { decidePresence, FocusWatch } from './presence'
import { ReminderBook, SitTimer } from './reminders'

/** 最长隔多久重新排一次闹钟（电脑睡眠后 setTimeout 会不准） */
const MAX_TIMER_MS = 60 * 60 * 1000

export interface DesktopKeeperOptions {
  dataDir: string
  character: () => BrowserWindow | null
  characterEvent: (ev: CharacterEvent) => void
  onChange: (status: DesktopStatus) => void
  loadPrefs: () => unknown
  savePrefs: (prefs: DesktopPrefs) => void
}

export class DesktopKeeper {
  prefs: DesktopPrefs
  dnd = false
  presence: Presence = { visible: true, quiet: false, reason: 'normal' }
  private focus: FocusState = 'normal'
  private book: ReminderBook
  private sit: SitTimer
  private readonly startedAt = Date.now()
  private alarm: NodeJS.Timeout | undefined
  private minute: NodeJS.Timeout | undefined
  private watch = new FocusWatch((f) => {
    this.focus = f
    this.applyPresence()
  })

  constructor(private o: DesktopKeeperOptions) {
    this.prefs = cleanPrefs(o.loadPrefs())
    this.book = new ReminderBook(this.readReminders())
    this.sit = new SitTimer(this.prefs.sitBreakMinutes)
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
    // 睡眠醒来、解锁：闹钟可能错过了，马上看一遍
    powerMonitor.on('resume', () => this.ring())
    powerMonitor.on('unlock-screen', () => this.ring())
    this.minute = setInterval(() => this.checkSitting(), 60_000)
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
      new Notification({ title: late ? `艾拉 · 刚才 ${when} 的提醒` : '艾拉 · 提醒', body: reminder.text }).show()
    }
  }

  private checkSitting(): void {
    if (!this.sit.tick(Date.now(), powerMonitor.getSystemIdleTime())) return
    // 全屏、演示、免打扰时不打扰（打游戏、开会时不需要她来催）
    if (this.presence.visible && !this.presence.quiet) {
      this.o.characterEvent({ kind: 'sit-break', minutes: this.prefs.sitBreakMinutes })
    }
  }

  // ---------- 在不在、省电、开机启动 ----------

  private applyPresence(force = false): void {
    const next = decidePresence(this.focus, this.dnd, this.prefs)
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
