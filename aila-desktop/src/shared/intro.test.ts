import { describe, expect, it } from 'vitest'
import {
  INTRO_MS,
  INTRO_SLIDES,
  PRESENTER_RATIO,
  cursorPoint,
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
  it('幻灯片：一本正经地用「您」，最后一页念到一半停下', () => {
    expect(INTRO_SLIDES.length).toBeGreaterThanOrEqual(3)
    for (const s of INTRO_SLIDES) expect(s.line).toContain('您')
    expect(INTRO_SLIDES[INTRO_SLIDES.length - 1].line.endsWith('……')).toBe(true)
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
