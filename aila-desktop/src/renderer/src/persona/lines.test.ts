import { afterEach, describe, expect, it, vi } from 'vitest'
import { isClose } from '../../../shared/chat'
import { lines, pick, setClose } from './lines'

afterEach(() => {
  setClose(false)
  vi.restoreAllMocks()
})

describe('熟了以后的台词', () => {
  it('认识两周、说过两百句以上才算很熟', () => {
    expect(isClose({ days: 30, talks: 500 })).toBe(true)
    expect(isClose({ days: 30, talks: 10 })).toBe(false) // 装上一个月不怎么聊
    expect(isClose({ days: 1, talks: 900 })).toBe(false)
    expect(isClose(null)).toBe(false)
  })

  it('不熟时只说原来的', () => {
    vi.spyOn(Math, 'random').mockReturnValue(0)
    expect(lines.pokeHead).toContain(pick(lines.pokeHead))
  })

  it('熟了一半时候换成熟了的说法；没有熟了的说法的照常', () => {
    setClose(true)
    vi.spyOn(Math, 'random').mockReturnValue(0)
    expect(lines.pokeHead).not.toContain(pick(lines.pokeHead))
    expect(pick(lines.pokeHead)).toBe('……又戳。头发真的会乱的。')
    expect(lines.dragStart).toContain(pick(lines.dragStart))
    vi.spyOn(Math, 'random').mockReturnValue(0.9)
    expect(lines.pokeHead).toContain(pick(lines.pokeHead))
  })
})
