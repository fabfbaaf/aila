import { describe, expect, it } from 'vitest'
import { shouldDraw, targetFps } from './frameBudget'

const base = { saver: true, onBattery: false, active: false, sinceMove: 60_000 }

describe('省电：帧率', () => {
  it('没人理她时降帧，鼠标在动时高一点，拔电再降', () => {
    expect(targetFps(base)).toBe(20)
    expect(targetFps({ ...base, sinceMove: 500 })).toBe(30)
    expect(targetFps({ ...base, onBattery: true })).toBe(12)
    expect(targetFps({ ...base, onBattery: true, sinceMove: 500 })).toBe(24)
  })

  it('在说话、在互动、或者关了省电：不限', () => {
    expect(targetFps({ ...base, active: true })).toBeNull()
    expect(targetFps({ ...base, active: true, onBattery: true })).toBeNull()
    expect(targetFps({ ...base, saver: false })).toBeNull()
  })

  it('60Hz 屏上限 30 帧：隔一帧画一次，不会掉成 20 帧', () => {
    const frame = 1000 / 60
    let last = 0
    let drawn = 0
    for (let i = 1; i <= 60; i++) {
      const now = i * frame
      if (shouldDraw(now, last, 30)) {
        drawn++
        last = now
      }
    }
    expect(drawn).toBe(30)
    expect(shouldDraw(5, 0, null)).toBe(true)
  })
})
