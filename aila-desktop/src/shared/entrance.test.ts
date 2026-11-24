import { describe, expect, it } from 'vitest'
import { CARD_SIZE, cardBounds, emergePosition, homePosition, jumpHeight, jumpPoint, shouldEnter } from './entrance'

const workArea = { x: 0, y: 0, width: 1920, height: 1040 }
const win = { width: 302, height: 482 }

describe('出场', () => {
  it('卡片在工作区正中间（多屏时按那块屏的坐标）', () => {
    expect(cardBounds(workArea)).toEqual({ x: 740, y: 370, ...CARD_SIZE })
    expect(cardBounds({ x: -1920, y: 100, width: 1920, height: 1040 }).x).toBe(-1180)
  })

  it('冒出来时窗口对着卡片中间，脚落在卡片里', () => {
    const card = cardBounds(workArea)
    const p = emergePosition(card, win)
    expect(p.x + win.width / 2).toBe(card.x + card.width / 2)
    const feet = p.y + win.height
    expect(feet).toBeGreaterThan(card.y + card.height / 2)
    expect(feet).toBeLessThan(card.y + card.height)
  })

  it('落点是右下角', () => {
    expect(homePosition(workArea, win)).toEqual({ x: 1920 - 302 - 40, y: 1040 - 482 })
  })

  it('弧线：两头是起点和终点，中间比两头都高（y 更小）', () => {
    const from = { x: 800, y: 400 }
    const to = { x: 1578, y: 558 }
    const h = jumpHeight(from, to, workArea)
    expect(jumpPoint(from, to, 0, h)).toEqual(from)
    expect(jumpPoint(from, to, 1, h)).toEqual(to)
    expect(jumpPoint(from, to, 2, h)).toEqual(to)
    const mid = jumpPoint(from, to, 0.5, h)
    expect(mid.y).toBeLessThan(from.y)
    expect(mid.x).toBeGreaterThan(from.x)
    expect(mid.x).toBeLessThan(to.x)
  })

  it('跳的高度：远的高，但不跳出屏幕顶', () => {
    const near = jumpHeight({ x: 0, y: 500 }, { x: 100, y: 500 }, workArea)
    const far = jumpHeight({ x: 0, y: 500 }, { x: 1000, y: 500 }, workArea)
    expect(far).toBeGreaterThan(near)
    expect(jumpHeight({ x: 0, y: 60 }, { x: 1000, y: 500 }, workArea)).toBe(60)
    // 窗口上面空着的那截可以伸出屏幕：起点再高也能跳起来
    expect(jumpHeight({ x: 0, y: 60 }, { x: 1000, y: 500 }, workArea, 200)).toBe(260)
  })

  it('设置关了、她不在桌面上、免打扰时不出场', () => {
    const on = { entrance: true, showCharacter: true, visible: true, dnd: false }
    expect(shouldEnter(on)).toBe(true)
    expect(shouldEnter({ ...on, entrance: false })).toBe(false)
    expect(shouldEnter({ ...on, showCharacter: false })).toBe(false)
    expect(shouldEnter({ ...on, visible: false })).toBe(false)
    expect(shouldEnter({ ...on, dnd: true })).toBe(false)
  })
})
