import { describe, expect, it } from 'vitest'
import { isEcho } from './echo'

describe('isEcho', () => {
  const saying = '今天北京晴，最高气温十五度，适合出门。'

  it('听到的是她正在说的话', () => {
    expect(isEcho('最高气温十五度', saying)).toBe(true)
    expect(isEcho('最高气温是十五度。', saying)).toBe(true) // 识别多了一个字
  })

  it('用户真的在插话', () => {
    expect(isEcho('等一下', saying)).toBe(false)
    expect(isEcho('不对，我问的是上海', saying)).toBe(false)
  })

  it('太短的不判断', () => {
    expect(isEcho('晴', saying)).toBe(false)
    expect(isEcho('好的', '')).toBe(false)
  })
})
