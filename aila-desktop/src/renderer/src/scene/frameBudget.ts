/**
 * 她一天到晚开着，透明置顶窗口每秒画 60 帧（高刷屏上更多）很费电。没人理她时降帧：
 * - 在说话、在听、对话框开着、气泡在显示、刚被点过或拖过：不限（动作要顺）
 * - 鼠标刚动过（视线在跟着转）：30 帧
 * - 都没有（只是呼吸、眨眼）：20 帧
 * - 笔记本拔了电：再往下降
 * 关掉「省电」就一直不限。窗口藏起来时 Chromium 本来就不画，不用管。
 */

export interface FrameState {
  /** 设置里的「省电」 */
  saver: boolean
  onBattery: boolean
  /** 在说话、在听、对话框开着、气泡在显示、刚被点过或拖过 */
  active: boolean
  /** 离上次鼠标移动过了多少毫秒 */
  sinceMove: number
}

/** 鼠标停下后多久算「没在动」 */
export const MOVE_GRACE_MS = 2000

/** 每秒最多画几帧；null = 不限 */
export function targetFps(s: FrameState): number | null {
  if (!s.saver || s.active) return null
  const moving = s.sinceMove < MOVE_GRACE_MS
  if (s.onBattery) return moving ? 24 : 12
  return moving ? 30 : 20
}

/** 这一帧要不要画：离上一次画过的时间够不够（留 2 毫秒余量，免得 60Hz 屏上 30 帧变成 20 帧） */
export function shouldDraw(now: number, lastDrawn: number, fps: number | null): boolean {
  return fps === null || now - lastDrawn >= 1000 / fps - 2
}
