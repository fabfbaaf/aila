import { describe, expect, it } from 'vitest'
import { CURSOR_SCRIPT, moveLine } from './cursor'

describe('借鼠标的帮手', () => {
  it('脚本只声明挪光标和 DPI 感知：没有点击、键盘', () => {
    expect(CURSOR_SCRIPT).toContain('SetCursorPos')
    for (const bad of ['mouse_event', 'SendInput', 'keybd_event', 'SendKeys', 'Invoke-Expression', 'iex ']) {
      expect(CURSOR_SCRIPT).not.toContain(bad)
    }
  })

  it('坐标取整成一行；不是数字就不发', () => {
    expect(moveLine(10.4, 20.6)).toBe('10 21\n')
    expect(moveLine(-5, 3)).toBe('-5 3\n')
    expect(moveLine(Number.NaN, 3)).toBeNull()
    expect(moveLine(1, Number.POSITIVE_INFINITY)).toBeNull()
  })
})
