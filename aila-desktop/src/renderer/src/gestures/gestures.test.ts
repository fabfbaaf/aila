import { describe, expect, it } from 'vitest'
import { classifyHand, isSeal, type HandPose, type Point } from './classify'
import { GestureEngine, HOLD_MS, toScreen } from './GestureEngine'

interface Shape {
  thumb?: boolean
  index?: boolean
  middle?: boolean
  ring?: boolean
  pinky?: boolean
  /** 拇指尖碰食指尖 */
  pinch?: boolean
  /** 食指和中指的指尖并在一起 */
  together?: boolean
  cx?: number
  cy?: number
  /** 手的大小（画面比例） */
  s?: number
}

/** 拼一只手的 21 个关键点：手指伸直就往上，弯着就往手心卷 */
function hand(shape: Shape): Point[] {
  const { cx = 0.5, cy = 0.75, s = 0.2 } = shape
  const lm: Point[] = new Array(21)
  lm[0] = { x: cx, y: cy }
  const bases = [cx - 0.15 * s, cx, cx + 0.15 * s, cx + 0.3 * s]
  const fan = [-0.1, 0, 0.1, 0.2]
  const flags = [shape.index, shape.middle, shape.ring, shape.pinky]
  flags.forEach((up, i) => {
    const mcp = { x: bases[i], y: cy - 0.5 * s }
    const at = 5 + i * 4
    lm[at] = mcp
    if (up) {
      const spread = fan[i] * s
      lm[at + 1] = { x: mcp.x + spread * 0.4, y: mcp.y - 0.25 * s }
      lm[at + 2] = { x: mcp.x + spread * 0.7, y: mcp.y - 0.4 * s }
      lm[at + 3] = { x: mcp.x + spread, y: mcp.y - 0.55 * s }
    } else {
      lm[at + 1] = { x: mcp.x, y: mcp.y - 0.15 * s }
      lm[at + 2] = { x: mcp.x, y: mcp.y - 0.05 * s }
      lm[at + 3] = { x: mcp.x, y: mcp.y + 0.05 * s }
    }
  })
  if (shape.together) lm[12] = { x: lm[8].x + 0.05 * s, y: lm[8].y }
  lm[1] = { x: cx - 0.2 * s, y: cy - 0.15 * s }
  lm[2] = { x: cx - 0.3 * s, y: cy - 0.25 * s }
  lm[3] = { x: cx - 0.38 * s, y: cy - 0.35 * s }
  lm[4] = shape.pinch
    ? { x: lm[8].x + 0.03 * s, y: lm[8].y + 0.03 * s }
    : shape.thumb
      ? { x: cx - 0.45 * s, y: cy - 0.45 * s }
      : { x: cx - 0.1 * s, y: cy - 0.4 * s }
  return lm
}

const OPEN: Shape = { thumb: true, index: true, middle: true, ring: true, pinky: true }
const FIST: Shape = {}
const SWORD: Shape = { index: true, middle: true, together: true }
const PINCH: Shape = { index: true, middle: true, ring: true, pinky: true, pinch: true }
const pose = (shape: Shape): HandPose => classifyHand(hand(shape))

describe('认手势', () => {
  it.each([
    [OPEN, 'open'],
    [FIST, 'fist'],
    [SWORD, 'sword'],
    [PINCH, 'pinch'],
    [{ index: true }, 'point'],
    [{ index: true, middle: true }, 'other'] // 两指分开（比耶）不是剑指
  ] as [Shape, string][])('%o → %s', (shape, expected) => {
    expect(pose(shape).pose).toBe(expected)
  })

  it('离摄像头远近不影响判断', () => {
    expect(pose({ ...SWORD, s: 0.08 }).pose).toBe('sword')
    expect(pose({ ...OPEN, s: 0.45 }).pose).toBe('open')
  })

  it('坐标左右镜像，和屏幕方向一致', () => {
    expect(pose({ ...OPEN, cx: 0.2 }).palm.x).toBeGreaterThan(0.7)
  })

  it('双手合十：两只手掌和指尖都靠在一起', () => {
    const left = pose({ ...OPEN, cx: 0.48 })
    const right = pose({ ...OPEN, cx: 0.52 })
    expect(isSeal(left, right)).toBe(true)
    expect(isSeal(pose({ ...OPEN, cx: 0.2 }), pose({ ...OPEN, cx: 0.8 }))).toBe(false)
    expect(isSeal(pose({ ...FIST, cx: 0.48 }), pose({ ...FIST, cx: 0.52 }))).toBe(false)
  })
})

/** 让引擎按固定间隔看同一个姿势，收集吐出来的事件 */
function hold(engine: GestureEngine, hands: HandPose[], from: number, ms: number, step = 33): string[] {
  const out: string[] = []
  for (let t = from; t <= from + ms; t += step) out.push(...engine.update(hands, t).map((e) => e.type))
  return out
}

describe('手势引擎', () => {
  it('剑指要保持一小会儿才唤出，只触发一次', () => {
    const e = new GestureEngine()
    expect(hold(e, [pose(SWORD)], 0, HOLD_MS.summon - 100)).toEqual([])
    expect(e.state.progress).toBeGreaterThan(0.5)
    expect(hold(e, [pose(SWORD)], HOLD_MS.summon - 67, 2000)).toEqual(['summon'])
  })

  it('一闪而过的握拳不算', () => {
    const e = new GestureEngine()
    const out = [...hold(e, [pose(FIST)], 0, 300), ...hold(e, [pose(OPEN)], 333, 300)]
    expect(out).not.toContain('dismiss')
  })

  it('触发后有冷却：马上再做一次不算', () => {
    const e = new GestureEngine()
    expect(hold(e, [pose(FIST)], 0, 1000)).toContain('dismiss')
    e.update([], 1033) // 手离开画面
    expect(hold(e, [pose(FIST)], 1066, 1000)).not.toContain('dismiss') // 还在冷却里（1.2 秒）
  })

  it('捏住抓星球，往面前拉展开面板，松开放下', () => {
    const e = new GestureEngine()
    const grabbed = e.update([pose(PINCH)], 0)
    expect(grabbed).toEqual([])
    const events = e.update([pose(PINCH)], HOLD_MS.grab + 10)
    expect(events[0]).toMatchObject({ type: 'grab' })
    expect(e.update([pose({ ...PINCH, s: 0.22 })], 400).map((x) => x.type)).toEqual([]) // 大了一点点不算
    expect(e.update([pose({ ...PINCH, s: 0.3 })], 450).map((x) => x.type)).toEqual(['pull'])
    expect(e.update([pose({ ...PINCH, s: 0.35 })], 500).map((x) => x.type)).toEqual([]) // 只拉一次
    expect(e.update([pose(OPEN)], 550).map((x) => x.type)).toContain('release')
  })

  it('张开手掌移动是旋转，手抖不算', () => {
    const e = new GestureEngine()
    e.update([pose({ ...OPEN, cx: 0.5 })], 0)
    expect(e.update([pose({ ...OPEN, cx: 0.5005 })], 33)).toEqual([]) // 死区里
    const [ev] = e.update([pose({ ...OPEN, cx: 0.45 })], 66)
    expect(ev.type).toBe('rotate')
    expect(ev.type === 'rotate' && ev.dx).toBeGreaterThan(0) // 手往画面左边移 = 屏幕上往右（镜像）
    expect(e.state.label).toContain('旋转')
  })

  it('双手张开拉开是放大（镜头拉近）', () => {
    const e = new GestureEngine()
    e.update([pose({ ...OPEN, cx: 0.4 }), pose({ ...OPEN, cx: 0.6 })], 0)
    const [ev] = e.update([pose({ ...OPEN, cx: 0.3 }), pose({ ...OPEN, cx: 0.7 })], 33)
    expect(ev).toMatchObject({ type: 'zoom' })
    expect(ev.type === 'zoom' && ev.factor).toBeLessThan(1)
  })

  it('双手合十保持一秒才确认', () => {
    const e = new GestureEngine()
    const seal = [pose({ ...OPEN, cx: 0.48 }), pose({ ...OPEN, cx: 0.52 })]
    expect(hold(e, seal, 0, HOLD_MS.seal - 100)).toEqual([])
    expect(e.state.label).toContain('结印')
    expect(hold(e, seal, HOLD_MS.seal - 67, 500)).toEqual(['seal'])
  })

  it('手离开画面：清空状态', () => {
    const e = new GestureEngine()
    e.update([pose(OPEN)], 0)
    e.update([], 33)
    expect(e.state).toEqual({ cursor: null, label: '', progress: 0, hands: 0 })
  })
})

describe('手势光标', () => {
  it('画面中间那块对应整个窗口，伸出去也不会跑出窗口', () => {
    expect(toScreen({ x: 0.5, y: 0.5 })).toEqual({ x: 0.5, y: 0.5 })
    expect(toScreen({ x: 0.15, y: 0.85 })).toEqual({ x: 0, y: 1 })
    expect(toScreen({ x: -0.2, y: 1.3 })).toEqual({ x: 0, y: 1 })
  })
})
