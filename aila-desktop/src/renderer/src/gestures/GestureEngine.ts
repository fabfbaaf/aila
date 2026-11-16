/**
 * 手势 → 星图里的动作。每帧喂进认出来的手，吐出要做的事。纯逻辑，可以单测。
 *
 * 一次性的手势（唤出、收起、结印、抓住）要保持一小会儿才算，防止手在画面里路过时误触发；
 * 触发后要先换个手势（或者手离开画面）才能再触发同一个，而且触发后有一小段冷却。
 * 连续的手势（旋转、缩放）每帧都给变化量，有个小死区，手抖不会让星图跟着抖。
 */
import { handsApart, isSeal, type HandPose, type Point } from './classify'

export type GestureEvent =
  | { type: 'summon' } // 剑指：唤出全图（镜头拉回来看整个星图）
  | { type: 'dismiss' } // 握拳：收起（先收面板，没有面板就关掉星图）
  | { type: 'seal' } // 双手合十：确认
  | { type: 'grab'; x: number; y: number } // 捏合：抓住手指下的星球（坐标是画面比例，已镜像）
  | { type: 'release' }
  | { type: 'pull' } // 捏住往面前拉：展开面板
  | { type: 'rotate'; dx: number; dy: number } // 张开手掌移动
  | { type: 'zoom'; factor: number } // 双手拉开（<1，拉近）/ 合拢（>1，拉远）

type Held = 'summon' | 'dismiss' | 'seal' | 'grab'

export interface GestureState {
  /** 手在屏幕上的位置（画面比例），没有手时为 null */
  cursor: Point | null
  /** 正在做的手势（给用户看） */
  label: string
  /** 一次性手势保持了多久（0–1，满了就触发） */
  progress: number
  hands: number
}

export const HOLD_MS: Record<Held, number> = { summon: 600, dismiss: 900, seal: 1000, grab: 250 }
const COOLDOWN_MS = 1200
const DEADZONE = 0.004 // 手掌每帧移动不到这么多不算
const PULL = 1.3 // 捏住后手变大到 1.3 倍（离摄像头近了）算往面前拉
const LABELS: Record<Held | 'rotate' | 'zoom' | 'holding', string> = {
  summon: '剑指 · 唤出全图',
  dismiss: '握拳 · 收起',
  seal: '结印 · 确认',
  grab: '捏合 · 抓住星球',
  holding: '抓着 · 往面前拉可以展开面板',
  rotate: '张开手掌 · 旋转',
  zoom: '双手 · 缩放'
}

/** 手很少伸到画面最边上：画面中间这一块就对应整个窗口 */
export const REACH = 0.7

/** 画面比例坐标（已镜像）→ 窗口比例坐标 */
export function toScreen(p: Point): Point {
  const f = (v: number): number => Math.min(1, Math.max(0, (v - (1 - REACH) / 2) / REACH))
  return { x: f(p.x), y: f(p.y) }
}

export class GestureEngine {
  state: GestureState = { cursor: null, label: '', progress: 0, hands: 0 }

  private candidate: Held | null = null
  private since = 0
  private fired = false
  private cooldownUntil = 0
  private lastPalm: Point | null = null
  private lastApart: number | null = null
  private grabbing: { size: number; pulled: boolean } | null = null

  update(hands: HandPose[], now: number): GestureEvent[] {
    const events: GestureEvent[] = []
    this.state.hands = hands.length
    if (hands.length === 0) {
      if (this.grabbing) events.push({ type: 'release' })
      this.reset()
      this.state = { cursor: null, label: '', progress: 0, hands: 0 }
      return events
    }

    const [a, b] = hands
    const two = hands.length >= 2
    const main = a
    this.state.cursor = main.pose === 'pinch' || main.pose === 'point' ? main.tip : main.palm

    // ---------- 一次性的手势：保持够久才算 ----------
    const want: Held | null = two
      ? isSeal(a, b)
        ? 'seal'
        : null
      : main.pose === 'sword'
        ? 'summon'
        : main.pose === 'fist'
          ? 'dismiss'
          : main.pose === 'pinch'
            ? 'grab'
            : null
    if (want !== this.candidate) {
      // 换了手势：重新计时；松开捏合算放下
      if (this.candidate === 'grab' && this.grabbing) events.push({ type: 'release' })
      this.candidate = want
      this.since = now
      this.fired = false
      this.grabbing = null
    }
    let progress = 0
    if (want && !this.fired) {
      progress = Math.min(1, (now - this.since) / HOLD_MS[want])
      if (progress >= 1 && now >= this.cooldownUntil) {
        this.fired = true
        if (want === 'grab') {
          events.push({ type: 'grab', x: main.tip.x, y: main.tip.y })
          this.grabbing = { size: main.size, pulled: false }
        } else {
          events.push({ type: want })
          this.cooldownUntil = now + COOLDOWN_MS
        }
      }
    }

    // ---------- 抓着的时候：往面前拉 ----------
    if (this.grabbing && !this.grabbing.pulled && main.size > this.grabbing.size * PULL) {
      this.grabbing.pulled = true
      events.push({ type: 'pull' })
    }

    // ---------- 连续的手势 ----------
    let label = want ? LABELS[want] : ''
    if (this.grabbing) label = LABELS.holding
    if (want === 'seal') {
      // 合十的时候两只手掌也是张开的，但这时候是在确认，不是缩放
      this.lastPalm = null
      this.lastApart = null
    } else if (two && a.pose === 'open' && b.pose === 'open') {
      const apart = handsApart(a, b)
      if (this.lastApart !== null && Math.abs(apart - this.lastApart) > 0.02) {
        events.push({ type: 'zoom', factor: this.lastApart / apart })
      }
      this.lastApart = apart
      this.lastPalm = null
      label = LABELS.zoom
    } else if (!two && main.pose === 'open') {
      if (this.lastPalm) {
        const dx = main.palm.x - this.lastPalm.x
        const dy = main.palm.y - this.lastPalm.y
        if (Math.hypot(dx, dy) > DEADZONE) events.push({ type: 'rotate', dx, dy })
      }
      this.lastPalm = main.palm
      this.lastApart = null
      label = LABELS.rotate
    } else {
      this.lastPalm = null
      this.lastApart = null
    }

    this.state.label = label
    this.state.progress = this.fired ? 0 : progress
    return events
  }

  private reset(): void {
    this.candidate = null
    this.fired = false
    this.grabbing = null
    this.lastPalm = null
    this.lastApart = null
  }
}
