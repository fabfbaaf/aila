import { describe, expect, it } from 'vitest'
import {
  INTRO_MS,
  INTRO_SLIDES,
  PRESENTER_RATIO,
  bumpPoint,
  cursorPoint,
  magicCenter,
  spawnCharge,
  particleAlive,
  seeded,
  shakeOffset,
  spawnDust,
  spawnShards,
  spawnSpark,
  stepParticle,
  escapeLanding,
  introMainBounds,
  knockPoint,
  presenterPosition,
  shouldIntro,
  shovedMainBounds,
  slideDuration,
  userTookOver
} from './intro'

const workArea = { x: 0, y: 0, width: 1920, height: 1040 }
const win = { width: 300, height: 480 }

describe('开场演出', () => {
  it('幻灯片：开头一本正经用「您」，越讲越随便，最后一页念到一半停下', () => {
    expect(INTRO_SLIDES.length).toBeGreaterThanOrEqual(6)
    for (const s of INTRO_SLIDES.slice(0, 4)) expect(s.line).toContain('您')
    expect(INTRO_SLIDES.some((s) => s.gesture === 'yawn')).toBe(true)
    expect(INTRO_SLIDES.some((s) => !s.line.includes('您'))).toBe(true)
    expect(INTRO_SLIDES[INTRO_SLIDES.length - 1].line.endsWith('……')).toBe(true)
    // 每页有自己的小演示，不重复
    expect(new Set(INTRO_SLIDES.map((s) => s.demo)).size).toBe(INTRO_SLIDES.length)
  })

  it('一页念多久：短的按最少时间，长的按字数', () => {
    expect(slideDuration('短')).toBe(INTRO_MS.slide)
    expect(slideDuration('字'.repeat(100))).toBeGreaterThan(INTRO_MS.slide)
  })

  it('主窗口在屏幕中间，小屏幕上不超出', () => {
    const b = introMainBounds(workArea)
    expect(b.x + b.width / 2).toBe(960)
    const small = introMainBounds({ x: 0, y: 0, width: 1024, height: 700 })
    expect(small.width).toBeLessThanOrEqual(1024)
    expect(small.height).toBeLessThanOrEqual(700)
  })

  it('讲解时她站在主窗口左边那一栏里，脚在窗口里', () => {
    const main = introMainBounds(workArea)
    const p = presenterPosition(main, win)
    const center = p.x + win.width / 2
    expect(center).toBeGreaterThan(main.x)
    expect(center).toBeLessThan(main.x + main.width * PRESENTER_RATIO)
    const feet = p.y + win.height
    expect(feet).toBeLessThan(main.y + main.height)
    expect(feet).toBeGreaterThan(main.y + main.height - 40)
  })

  it('敲屏幕的点换算成主窗口里的坐标，落在窗口里', () => {
    const main = introMainBounds(workArea)
    const k = knockPoint(presenterPosition(main, win), win, main)
    expect(k.x).toBeGreaterThan(0)
    expect(k.x).toBeLessThan(main.width)
    expect(k.y).toBeGreaterThan(0)
    expect(k.y).toBeLessThan(main.height)
  })

  it('碎了以后主窗口缩小挤到左上；她落在屏幕右边的地上', () => {
    const main = introMainBounds(workArea)
    const s = shovedMainBounds(main, workArea)
    expect(s.width).toBeLessThan(main.width)
    expect(s.x).toBeLessThan(main.x)
    const land = escapeLanding(workArea, win)
    expect(land.y + win.height).toBe(workArea.height)
    expect(land.x + win.width / 2).toBeGreaterThan(workArea.width / 2)
  })

  it('光标两头是起点终点，中间在两点之间', () => {
    const a = { x: 100, y: 100 }
    const b = { x: 900, y: 500 }
    expect(cursorPoint(a, b, 0)).toEqual(a)
    expect(cursorPoint(a, b, 1)).toEqual(b)
    expect(cursorPoint(a, b, -1)).toEqual(a)
    const mid = cursorPoint(a, b, 0.5)
    expect(mid).toEqual({ x: 500, y: 300 })
    // 刚开始慢：走了一成时间还没走到一成路
    expect(cursorPoint(a, b, 0.1).x - a.x).toBeLessThan((b.x - a.x) * 0.1)
  })

  it('你一动鼠标（离她放的地方远了）就算抢回去了；差几个像素不算', () => {
    expect(userTookOver({ x: 500, y: 500 }, { x: 503, y: 498 })).toBe(false)
    expect(userTookOver({ x: 500, y: 500 }, { x: 560, y: 500 })).toBe(true)
  })

  it('只在第一次、她在桌面上时演', () => {
    expect(shouldIntro({ introDone: false, showCharacter: true, visible: true })).toBe(true)
    expect(shouldIntro({ introDone: true, showCharacter: true, visible: true })).toBe(false)
    expect(shouldIntro({ introDone: false, showCharacter: false, visible: true })).toBe(false)
    expect(shouldIntro({ introDone: false, showCharacter: true, visible: false })).toBe(false)
  })
})

describe('全屏特效', () => {
  const rect = { x: 100, y: 100, width: 800, height: 500 }
  const origin = { x: 300, y: 400 }

  it('同一个种子出来的碎片一样', () => {
    expect(spawnShards(rect, origin, 5, seeded(7))).toEqual(spawnShards(rect, origin, 5, seeded(7)))
    expect(spawnShards(rect, origin, 5, seeded(7))).not.toEqual(spawnShards(rect, origin, 5, seeded(8)))
  })

  it('碎片从窗口里出来，往远离裂纹的方向飞，大多先往上蹦', () => {
    const shards = spawnShards(rect, origin, 60, seeded(1))
    expect(shards).toHaveLength(60)
    for (const s of shards) {
      expect(s.x).toBeGreaterThanOrEqual(rect.x)
      expect(s.x).toBeLessThanOrEqual(rect.x + rect.width)
      expect(s.shape).toHaveLength(3)
      if (Math.abs(s.x - origin.x) > 1) expect(Math.sign(s.vx)).toBe(Math.sign(s.x - origin.x))
    }
    expect(shards.filter((s) => s.vy < 0).length).toBeGreaterThan(30)
  })

  it('碎片受重力往下掉，最后掉出屏幕或者到寿命就不画了', () => {
    let p = spawnShards(rect, origin, 1, seeded(3))[0]
    const vy0 = p.vy
    p = stepParticle(p, 0.1)
    expect(p.vy).toBeGreaterThan(vy0)
    for (let i = 0; i < 60; i++) p = stepParticle(p, 0.05)
    expect(particleAlive(p, 1040)).toBe(false)
  })

  it('灰往两边散、往上飘；火花在轨迹附近', () => {
    const dust = spawnDust({ x: 500, y: 900 }, 20, seeded(2))
    expect(dust.some((d) => d.vx < 0) && dust.some((d) => d.vx > 0)).toBe(true)
    for (const d of dust) expect(d.vy).toBeLessThan(0)
    const s = spawnSpark({ x: 200, y: 200 }, seeded(4))
    expect(Math.abs(s.x - 200)).toBeLessThanOrEqual(20)
  })

  it('窗口抖动越来越小，结束时回到原位', () => {
    expect(shakeOffset(0, 10)).toEqual({ x: 0, y: 5 })
    expect(shakeOffset(1, 10)).toEqual({ x: 0, y: 0 })
    const early = Math.max(...[0.05, 0.1, 0.15].map((t) => Math.abs(shakeOffset(t, 10).x)))
    const late = Math.max(...[0.85, 0.9, 0.95].map((t) => Math.abs(shakeOffset(t, 10).x)))
    expect(late).toBeLessThan(early)
  })
})

describe('用魔法破壁', () => {
  const main = introMainBounds(workArea)
  const stand = presenterPosition(main, win)

  it('第一次试：往左走到身子贴着窗口边，撞到的点在主窗口左边缘、她胸口的高度', () => {
    const b = bumpPoint(stand, win, main)
    expect(b.to.x).toBeLessThan(stand.x)
    expect(b.to.x + win.width / 2).toBe(main.x + 40)
    expect(b.to.y).toBe(stand.y)
    expect(b.contact.x).toBeLessThan(40)
    expect(b.contact.y).toBeGreaterThan(0)
    expect(b.contact.y).toBeLessThan(main.height)
  })

  it('法阵在她手的前面，在主窗口里', () => {
    const c = magicCenter(stand, win, main)
    const hand = knockPoint(stand, win, main)
    expect(c.x).toBeGreaterThan(hand.x)
    expect(c.x).toBeLessThan(main.width)
    expect(c.y).toBeGreaterThan(0)
  })

  it('蓄力的光点从四周出发，寿命走完正好到中心', () => {
    const center = { x: 500, y: 400 }
    const ps = spawnCharge(center, 30, seeded(5))
    expect(ps).toHaveLength(30)
    for (let p of ps) {
      expect(Math.hypot(p.x - center.x, p.y - center.y)).toBeGreaterThan(200)
      const life = p.life
      p = stepParticle(p, life - 0.0001)
      expect(Math.hypot(p.x - center.x, p.y - center.y)).toBeLessThan(2)
    }
  })
})
