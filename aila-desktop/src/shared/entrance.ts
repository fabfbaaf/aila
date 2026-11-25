/**
 * 打开艾拉时的出场：屏幕中间先出来一张「艾拉」卡片（像应用在启动），她从卡片里冒出来，
 * 一跳落到屏幕右下角，落地后挥手打招呼，卡片淡出。
 *
 * 主进程按这里的时间表挪角色窗口、通知两边的页面；这里只放能单测的纯计算（位置、弧线、要不要出场），
 * 动画本身在页面里（卡片：shell/LaunchCard.vue；她：App.vue 收到 character:entrance 时做的姿态）。
 */

export interface Point {
  x: number
  y: number
}

export interface Rect extends Point {
  width: number
  height: number
}

/** 各段多长（毫秒） */
export const ENTRANCE_MS = {
  /** 卡片出来到她开始冒头 */
  card: 700,
  /** 从卡片里冒出来 */
  emerge: 650,
  /** 起跳前蹲一下 */
  crouch: 220,
  /** 空中 */
  jump: 1050,
  /** 卡片淡出 */
  fade: 420,
  /** 页面一直没准备好（模型加载失败、卡住）就不演了，直接站到右下角 */
  timeout: 12000
} as const

/** 卡片大小 */
export const CARD_SIZE = { width: 440, height: 300 } as const

/** 卡片放在工作区正中间 */
export function cardBounds(workArea: Rect): Rect {
  return {
    x: Math.round(workArea.x + (workArea.width - CARD_SIZE.width) / 2),
    y: Math.round(workArea.y + (workArea.height - CARD_SIZE.height) / 2),
    ...CARD_SIZE
  }
}

/** 她平时站的地方：右下角（和「回到右下角」一样） */
export function homePosition(workArea: Rect, win: { width: number; height: number }): Point {
  return { x: workArea.x + workArea.width - win.width - 40, y: workArea.y + workArea.height - win.height }
}

/**
 * 刚从卡片里冒出来时角色窗口的位置：窗口水平居中对着卡片，她的脚（窗口底边）落在卡片中间偏下，
 * 看起来是站在卡片里、从卡片里长出来的。
 */
export function emergePosition(card: Rect, win: { width: number; height: number }): Point {
  return {
    x: Math.round(card.x + card.width / 2 - win.width / 2),
    y: Math.round(card.y + card.height * 0.72 - win.height)
  }
}

/**
 * 跳起来有多高：跳得越远越高，但她的头不会跳出屏幕顶。
 * headroom：窗口上面空着的那截（她站在窗口下半部分，上面是给说话气泡留的透明区），这一截可以伸到屏幕外面去。
 */
export function jumpHeight(from: Point, to: Point, workArea: Rect, headroom = 0): number {
  const dist = Math.hypot(to.x - from.x, to.y - from.y)
  const want = Math.min(420, Math.max(180, dist * 0.45))
  const room = Math.min(from.y, to.y) - workArea.y + headroom
  return Math.max(0, Math.min(want, room))
}

/**
 * 空中第 t（0~1）时刻的位置：水平方向先快后慢（落地前收一下），竖直方向是直线加一条抛物线。
 * t 超出 0~1 时按端点算。
 */
export function jumpPoint(from: Point, to: Point, t: number, height: number): Point {
  const k = Math.min(1, Math.max(0, t))
  const ease = 1 - Math.pow(1 - k, 1.6)
  return {
    x: Math.round(from.x + (to.x - from.x) * ease),
    y: Math.round(from.y + (to.y - from.y) * k - 4 * height * k * (1 - k))
  }
}

/** 要不要出场：设置里开着、她在桌面上显示、没开免打扰、不是全屏或演示（这时连她都藏着） */
export function shouldEnter(o: { entrance: boolean; showCharacter: boolean; visible: boolean; dnd: boolean }): boolean {
  return o.entrance && o.showCharacter && o.visible && !o.dnd
}

/** 主进程发给角色页面的出场阶段 */
export type EntrancePhase = 'emerge' | 'crouch' | 'jump' | 'land'
/** 开场演出（shared/intro.ts）里还会让她先藏起来 */
export type StagePhase = EntrancePhase | 'hide'
