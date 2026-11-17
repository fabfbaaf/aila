/** 金额输入和展示不能把零上限丢掉，也不能把小额费用显示为零。 */
import { describe, expect, it } from 'vitest'
import { parseBudget, validBudget, yuan } from './budget'

describe('任务金额', () => {
  it('空值不限，零和小额有效，拒绝非有限或负数', () => {
    expect(parseBudget('  ')).toBeNull()
    expect(parseBudget('0')).toBe(0)
    expect(parseBudget('.004')).toBe(0.004)
    expect(validBudget(null)).toBe(true)
    for (const value of [undefined, NaN, Infinity, -1, '0', false]) expect(validBudget(value)).toBe(false)
    for (const value of ['-1', 'Infinity', 'NaN', '1e309', '乱写', '0x10']) expect(() => parseBudget(value)).toThrow('有限的非负金额')
  })

  it('已知零和小额分别显示，正费用低于显示精度时提示范围', () => {
    expect(yuan(0)).toBe('¥0.00')
    expect(yuan(0.004)).toBe('¥0.004')
    expect(yuan(0.5)).toBe('¥0.50')
    expect(yuan(0.0000001)).toBe('不到 ¥0.000001')
  })
})
