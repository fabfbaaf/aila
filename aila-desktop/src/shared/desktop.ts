/**
 * 桌面上的她怎么「不挡事」、怎么省电、怎么提醒你：主进程、预加载和渲染层共用的类型。
 *
 * - 全屏应用、演示时她自己躲开（Windows 判断「现在适不适合打扰」的系统接口）；免打扰手动藏起来
 * - 没人理她时降帧，笔记本拔电再降
 * - 提醒：对话里说「25 分钟后叫我」，AI 服务算好时间推一个 reminder 事件，主进程记下来、到点叫你
 */

export interface DesktopPrefs {
  /** 全屏应用、演示时自动藏起来 */
  hideOnFullscreen: boolean
  /** 没人理她时降帧（拔电时再降） */
  powerSaver: boolean
  /** 开机时启动（打包装好以后才能用） */
  openAtLogin: boolean
  /** 连续用电脑多少分钟提醒起来活动；0 = 不提醒 */
  sitBreakMinutes: number
}

export const DEFAULT_DESKTOP_PREFS: DesktopPrefs = {
  hideOnFullscreen: true,
  powerSaver: true,
  openAtLogin: false,
  sitBreakMinutes: 0
}

export const SIT_BREAK_CHOICES = [0, 30, 45, 60, 90] as const

/** 系统说的「现在适不适合打扰」：normal 可以；fullscreen 全屏应用（游戏、视频、全屏的浏览器）；presentation 演示模式；away 锁屏或屏保 */
export type FocusState = 'normal' | 'fullscreen' | 'presentation' | 'away'

/** 她现在怎么样：visible 在不在桌面上；quiet 能不能主动说话（提醒改用系统通知） */
export interface Presence {
  visible: boolean
  quiet: boolean
  reason: 'normal' | 'fullscreen' | 'presentation' | 'away' | 'dnd'
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
  return {
    hideOnFullscreen: bool('hideOnFullscreen'),
    powerSaver: bool('powerSaver'),
    openAtLogin: bool('openAtLogin'),
    sitBreakMinutes: minutes
  }
}
