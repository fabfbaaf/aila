import { describe, expect, it } from 'vitest'
import { NUDGE_STEP, SIDE_MARGIN, STROLL_MAX, STROLL_MIN, isMoveTarget, parseMove, shouldStroll, strollTarget, targetPosition, walkDuration, walkPoint } from './move'

describe('认走动的指令', () => {
  it('整句就是让她去某个位置', () => {
    expect(parseMove('去右上角')).toBe('topright')
    expect(parseMove('你去左上角待着吧！')).toBe('topleft')
    expect(parseMove('到屏幕中间来')).toBe('center')
    expect(parseMove('躲到左边去')).toBe('left')
    expect(parseMove('去最上面')).toBe('top')
    expect(parseMove('跑到下边去')).toBe('bottom')
    expect(parseMove('回右下角')).toBe('bottomright')
    expect(parseMove('右下角')).toBe('bottomright')
    expect(parseMove('艾拉 去 右边 呀~')).toBe('right')
    expect(parseMove('回去吧')).toBe('bottomright')
  })

  it('往哪边挪一点', () => {
    expect(parseMove('往左一点')).toBe('nudge-left')
    expect(parseMove('再往右挪挪')).toBe('nudge-right')
    expect(parseMove('往上点')).toBe('nudge-up')
    expect(parseMove('向下移一下')).toBe('nudge-down')
  })

  it('英文的说法也认', () => {
    expect(parseMove('Go to the top right corner.')).toBe('topright')
    expect(parseMove('please move to the left')).toBe('left')
    expect(parseMove('head to the middle of the screen')).toBe('center')
    expect(parseMove('Move left a bit')).toBe('nudge-left')
    expect(parseMove('scoot a little to the right')).toBe('nudge-right')
    expect(parseMove('go back')).toBe('bottomright')
    expect(parseMove('the button in the top right is broken')).toBeNull()
    expect(parseMove('go to the moon')).toBeNull()
  })

  it('只是提到位置的话不算', () => {
    expect(parseMove('我右下角有个按钮点不了')).toBeNull()
    expect(parseMove('上')).toBeNull() // 一个字太含糊
    expect(parseMove('左边')).toBeNull() // 没说「去」，也没说「角」
    expect(parseMove('去吃饭')).toBeNull()
    expect(parseMove('右上角的通知是什么意思，帮我看看这个报错')).toBeNull()
    expect(parseMove('')).toBeNull()
  })

  it('只认名单里的目标（主进程收到渲染层传的值也用它检查）', () => {
    expect(isMoveTarget('topright')).toBe(true)
    expect(isMoveTarget('nudge-up')).toBe(true)
    expect(isMoveTarget('moon')).toBe(false)
    expect(isMoveTarget(3)).toBe(false)
  })
})

describe('算位置', () => {
  const area = { x: 0, y: 0, width: 1920, height: 1040 }
  const me = { x: 1500, y: 600, width: 380, height: 440 }

  it('九个位置都在工作区里，右下和原来「回右下角」一样', () => {
    expect(targetPosition('bottomright', me, area)).toEqual({ x: 1920 - 380 - SIDE_MARGIN, y: 1040 - 440 })
    expect(targetPosition('topleft', me, area)).toEqual({ x: SIDE_MARGIN, y: 0 })
    expect(targetPosition('center', me, area)).toEqual({ x: 770, y: 300 })
    // 第二块屏幕在左边、坐标是负的
    const left = { x: -1280, y: 100, width: 1280, height: 984 }
    expect(targetPosition('topright', me, left)).toEqual({ x: -380 - SIDE_MARGIN, y: 100 })
  })

  it('挪一点不出屏幕', () => {
    expect(targetPosition('nudge-left', me, area)).toEqual({ x: 1500 - NUDGE_STEP, y: 600 })
    expect(targetPosition('nudge-right', me, area)).toEqual({ x: 1920 - 380, y: 600 })
    expect(targetPosition('nudge-down', me, area)).toEqual({ x: 1500, y: 600 })
  })
})

describe('走过去', () => {
  const a = { x: 0, y: 0 }
  const b = { x: 1000, y: 400 }

  it('起点终点准确，中间有小颠，时长按距离有上下限', () => {
    expect(walkPoint(a, b, 0)).toEqual(a)
    expect(walkPoint(a, b, 1)).toEqual(b)
    expect(walkPoint(a, b, 2)).toEqual(b)
    const mid = walkPoint(a, b, 0.5)
    expect(mid.x).toBe(500)
    expect(walkPoint(a, b, 0.25).y).toBeLessThan(400 * 0.0625 + 1) // 开头慢，还往上颠了一下
    expect(walkDuration(a, { x: 10, y: 0 })).toBe(350)
    expect(walkDuration(a, { x: 5000, y: 0 })).toBe(1600)
  })
})

describe('没事时走走', () => {
  const area = { x: 0, y: 0, width: 1920, height: 1040 }
  const fixed = (...vals: number[]) => { let i = 0; return () => vals[i++ % vals.length] }

  it('在原来的高度上往左右走一小段，靠边时往回走，不出屏幕', () => {
    const mid = { x: 900, y: 600, width: 380, height: 440 }
    expect(strollTarget(mid, area, fixed(0, 0.2))).toEqual({ x: 900 - STROLL_MIN, y: 600 }) // 往左
    expect(strollTarget(mid, area, fixed(1, 0.9))).toEqual({ x: 900 + STROLL_MAX, y: 600 }) // 往右
    const atRight = { x: 1920 - 380 - 40, y: 600, width: 380, height: 440 }
    expect(strollTarget(atRight, area, fixed(0.5, 0.9)).x).toBeLessThan(atRight.x)
    const atLeft = { x: 10, y: 600, width: 380, height: 440 }
    expect(strollTarget(atLeft, area, fixed(0.5, 0.1)).x).toBeGreaterThan(atLeft.x)
    expect(isMoveTarget('stroll')).toBe(true)
  })

  it('开了开关、她在桌面上、没在聊没在睡，才碰运气走一次', () => {
    const ok = { enabled: true, visible: true, quiet: false, chatting: false, asleep: false }
    expect(shouldStroll(ok, () => 0.1)).toBe(true)
    expect(shouldStroll(ok, () => 0.9)).toBe(false)
    for (const k of ["enabled", "visible"] as const) expect(shouldStroll({ ...ok, [k]: false }, () => 0)).toBe(false)
    for (const k of ["quiet", "chatting", "asleep"] as const) expect(shouldStroll({ ...ok, [k]: true }, () => 0)).toBe(false)
  })
})
