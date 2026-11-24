/**
 * 桌面上的她怎么「不挡事」、怎么省电、怎么提醒你：主进程、预加载和渲染层共用的类型。
 *
 * - 全屏应用、演示时她自己躲开（Windows 判断「现在适不适合打扰」的系统接口）；免打扰手动藏起来
 * - 没人理她时降帧，笔记本拔电再降
 * - 提醒：对话里说「25 分钟后叫我」，AI 服务算好时间推一个 reminder 事件，主进程记下来、到点叫你
 */

import { WORKS_BUDGET_CHOICES, WORKS_EVERY_CHOICES } from './studio'

export interface DesktopPrefs {
  /**
   * 桌面上显示她。关掉以后角色窗口一直藏着：主窗口、托盘、编程任务、语音唤醒照常用，
   * 提醒改成系统通知（和免打扰一样），打开艾拉时直接开主窗口
   */
  showCharacter: boolean
  /** 全屏应用、演示时自动藏起来 */
  hideOnFullscreen: boolean
  /** 没人理她时降帧（拔电时再降） */
  powerSaver: boolean
  /** 开机时启动（打包装好以后才能用） */
  openAtLogin: boolean
  /** 连续用电脑多少分钟提醒起来活动；0 = 不提醒 */
  sitBreakMinutes: number
  /** 晚上几点说今天的日报（HH:MM）；'' = 不说 */
  reportAt: string
  /** 她自己做作品：平均几天一件（0 = 不让她做）。会花钱，默认不开 */
  worksEvery: number
  /** 每件作品最多花多少元 */
  worksBudget: number
  /** 每天晚上写日记 */
  diary: boolean
  /** 没事时自己在屏幕上走两步（你在打字、全屏、和她说话时不动） */
  wander: boolean
  /** 打开艾拉时的出场：先出来一张「艾拉」卡片，她从里面跳出来，落到右下角打招呼 */
  entrance: boolean
  /** 每天看一次 GitHub 上有没有新版本（装好的版本才查） */
  checkUpdates: boolean
  /** 每周五晚上写这周的回顾（作品页「回顾」里看；会花一点钱，一周一次） */
  weeklyReview: boolean
}

export const DEFAULT_DESKTOP_PREFS: DesktopPrefs = {
  showCharacter: true,
  hideOnFullscreen: true,
  powerSaver: true,
  openAtLogin: false,
  sitBreakMinutes: 0,
  reportAt: '',
  worksEvery: 0,
  worksBudget: 0.5,
  diary: true,
  wander: false,
  entrance: true,
  checkUpdates: true,
  weeklyReview: true
}

export const SIT_BREAK_CHOICES = [0, 30, 45, 60, 90] as const
/** 晚上几点说日报；'' = 不说 */
export const REPORT_TIMES = ['', '17:30', '18:00', '19:00', '21:00'] as const

/** 系统说的「现在适不适合打扰」：normal 可以；fullscreen 全屏应用（游戏、视频、全屏的浏览器）；presentation 演示模式；away 锁屏或屏保 */
export type FocusState = 'normal' | 'fullscreen' | 'presentation' | 'away'

/** 她现在怎么样：visible 在不在桌面上；quiet 能不能主动说话（提醒改用系统通知） */
export interface Presence {
  visible: boolean
  quiet: boolean
  /** off：设置里关了「桌面上显示她」 */
  reason: 'normal' | 'fullscreen' | 'presentation' | 'away' | 'dnd' | 'off'
}

export interface Reminder {
  id: string
  /** 到点的时间（ISO 字符串，带时区） */
  at: string
  text: string
  createdAt: string
}

export interface DesktopStatus {
  prefs: DesktopPrefs
  presence: Presence
  /** 免打扰（手动藏起来） */
  dnd: boolean
  onBattery: boolean
  /** 开机启动只有打包装好的版本能设 */
  loginSupported: boolean
  reminders: Reminder[]
}

/** 设置页传过来的偏好先检查一遍（只认上面这些字段和取值） */
export function cleanPrefs(raw: unknown, base: DesktopPrefs = DEFAULT_DESKTOP_PREFS): DesktopPrefs {
  const r = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>
  const bool = (k: keyof DesktopPrefs): boolean => (typeof r[k] === 'boolean' ? (r[k] as boolean) : (base[k] as boolean))
  const minutes = SIT_BREAK_CHOICES.includes(r.sitBreakMinutes as (typeof SIT_BREAK_CHOICES)[number])
    ? (r.sitBreakMinutes as number)
    : base.sitBreakMinutes
  const reportAt = REPORT_TIMES.includes(r.reportAt as (typeof REPORT_TIMES)[number]) ? (r.reportAt as string) : base.reportAt
  const pick = <T extends number>(k: keyof DesktopPrefs, choices: readonly T[]): T =>
    choices.includes(r[k] as T) ? (r[k] as T) : (base[k] as T)
  return {
    showCharacter: bool('showCharacter'),
    hideOnFullscreen: bool('hideOnFullscreen'),
    powerSaver: bool('powerSaver'),
    openAtLogin: bool('openAtLogin'),
    sitBreakMinutes: minutes,
    reportAt,
    worksEvery: pick('worksEvery', WORKS_EVERY_CHOICES),
    worksBudget: pick('worksBudget', WORKS_BUDGET_CHOICES),
    diary: bool('diary'),
    wander: bool('wander'),
    entrance: bool('entrance'),
    checkUpdates: bool('checkUpdates'),
    weeklyReview: bool('weeklyReview')
  }
}
