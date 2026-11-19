import { describe, expect, it } from 'vitest'
import { cleanUpdate, summarize, validExtensionId, validStageToken } from './extensions'

describe('扩展包', () => {
  it('id 和暂存号', () => {
    expect(validExtensionId('git-helper')).toBe(true)
    for (const bad of ['Git', '../x', '', 'a'.repeat(25), 3]) expect(validExtensionId(bad)).toBe(false)
    expect(validStageToken('0123456789abcdef')).toBe(true)
    expect(validStageToken('../../etc')).toBe(false)
  })

  it('渲染层发来的修改只收认识的字段', () => {
    expect(cleanUpdate({ enabled: false })).toEqual({ enabled: false })
    expect(cleanUpdate({ settings: { token: 'abc' }, servers: { srv: true } })).toEqual({ settings: { token: 'abc' }, servers: { srv: true } })
    for (const bad of [null, [], {}, { enabled: 'yes' }, { settings: { '1x': 'a' } }, { settings: { k: 3 } },
      { servers: { 'toolong12': true } }, { servers: { s: 'off' } }, { settings: { k: 'x'.repeat(2001) } }]) {
      expect(cleanUpdate(bad)).toBeNull()
    }
    // 不认识的字段丢掉
    expect(cleanUpdate({ enabled: true, folder: 'C:/' })).toEqual({ enabled: true })
  })

  it('一行小结', () => {
    expect(summarize({ commands: ['a', 'b'], skills: [{ name: 's', description: '' }], servers: [] })).toBe('2 个命令 · 1 项本事')
    const server = { name: 's', serverName: 'x-s', disabled: true, readOnly: true, status: 'disabled' as const, error: '', command: '', tools: [], log: [] }
    expect(summarize({ commands: [], skills: [], servers: [server] })).toBe('1 个工具服务（都停着）')
    expect(summarize({ commands: [], skills: [], servers: [{ ...server, disabled: false }] })).toBe('1 个工具服务（开着 1 个）')
    expect(summarize({ commands: [], skills: [], servers: [] })).toBe('里面什么都没有')
  })
})
