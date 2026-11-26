import { describe, expect, it } from 'vitest'
import { cleanDiscoveryRule } from './discoveries'

describe('关注规则', () => {
  const rule = { source: 'gemini', focuses: ['pricing', 'web'], delivery: 'history', preference: '  只关心降价  ' }

  it('在主进程接收多种更新和只存历史的设置', () => {
    expect(cleanDiscoveryRule(rule)).toEqual({ ...rule, preference: '只关心降价' })
  })

  it('拒绝未知来源、空分类、超长条件和无效频率', () => {
    for (const patch of [
      { source: 'https://example.com' }, { focuses: [] }, { focuses: ['unrecognized'] },
      { preference: 'a'.repeat(181) }, { delivery: 'hourly' }
    ]) expect(cleanDiscoveryRule({ ...rule, ...patch })).toBeNull()
  })
})
