import { describe, expect, it } from 'vitest'
import { trayIcon } from './trayIcon'

const alpha = (buf: Buffer, size: number, x: number, y: number): number => buf[(y * size + x) * 4 + 3]

describe('托盘图标', () => {
  it('圆环：角上透明、环上不透明、中间挖空', () => {
    const size = 32
    const buf = trayIcon(size)
    expect(buf.length).toBe(size * size * 4)
    expect(alpha(buf, size, 0, 0)).toBe(0)
    expect(alpha(buf, size, 4, 16)).toBe(255)
    expect(alpha(buf, size, 16, 16)).toBeLessThan(10)
  })

  it('颜色按透明度预乘过（不会超过透明度）', () => {
    const buf = trayIcon(16)
    for (let i = 0; i < buf.length; i += 4) {
      expect(buf[i]).toBeLessThanOrEqual(buf[i + 3])
      expect(buf[i + 2]).toBeLessThanOrEqual(buf[i + 3])
    }
  })
})
