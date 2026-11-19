import { describe, expect, it } from 'vitest'
import { avatarPathOk, cleanMotion, findAvatar, validMotionName, validPageName, cleanMotions, cleanSources, cleanUpdate, mergeLines, summarize, validExtensionId, validStageToken } from './extensions'

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
    expect(summarize({ commands: [], skills: [], servers: [], motions: [{ name: 'cheer', label: '' }], lines: { wake: 2, pokeHead: 1 }, pages: [{ name: 'p', title: 'p' }] }))
      .toBe('1 个动作 · 3 句台词 · 1 个页面')
  })
})

describe('扩展的动作和台词', () => {
  it('动作：值截到范围里，不认识的键丢掉，太长、格式不对的不要', () => {
    expect(cleanMotion([{ dur: 0.3, pose: { headYaw: 5, leftUpperArm: 1 }, expr: { happy: 2, angry: 1 } }])).toEqual([
      { dur: 0.3, pose: { headYaw: 0.8 }, expr: { happy: 1 } }
    ])
    for (const bad of [[], [{ dur: 0 }], [{ dur: 9 }], 'x', [{ dur: 4 }, { dur: 4 }, { dur: 4 }]]) expect(cleanMotion(bad)).toBeNull()
    const motions = cleanMotions({ cheer: { label: '欢呼', phases: [{ dur: 1 }] }, nod: { label: '', phases: [{ dur: 1 }] }, 'Bad!': { label: '', phases: [{ dur: 1 }] } }, ['nod'])
    expect(Object.keys(motions)).toEqual(['cheer'])
  })

  it('台词：加进去、整组换掉，只动本来就有的池子', () => {
    const base = { pokeHead: ['呜'], wake: ['……啊'] }
    expect(mergeLines(base, { add: { pokeHead: ['又戳？', ''], chatError: ['x'] }, replace: {} })).toEqual({ pokeHead: ['呜', '又戳？'], wake: ['……啊'] })
    expect(mergeLines(base, { add: {}, replace: { wake: ['早'] } }).wake).toEqual(['早'])
    expect(mergeLines(base, undefined)).toEqual({ pokeHead: ['呜'], wake: ['……啊'] })
  })

  it('扩展的形象：路径必须在那个扩展的 avatars 文件夹里', () => {
    const file = 'C:\\Users\\me\\AppData\\Roaming\\aila-desktop\\ai\\data\\extensions\\cute\\avatars\\小艾.vrm'
    expect(avatarPathOk(file, 'cute', '小艾')).toBe(true)
    expect(avatarPathOk('C:\\Windows\\evil.vrm', 'cute', '小艾')).toBe(false)
    expect(avatarPathOk('/x/extensions/other/avatars/小艾.vrm', 'cute', '小艾')).toBe(false)
    expect(avatarPathOk('/x/extensions/cute/avatars/../../../secret/extensions/cute/avatars/小艾.vrm', 'cute', '小艾')).toBe(false)
    // Live2D：avatars/<名字>/<随便>.model3.json，只认那一层
    expect(avatarPathOk('C:\\d\\extensions\\cute\\avatars\\hiyori\\Hiyori.model3.json', 'cute', 'hiyori')).toBe(true)
    expect(avatarPathOk('C:\\d\\extensions\\cute\\avatars\\hiyori\\sub\\x.model3.json', 'cute', 'hiyori')).toBe(false)
    expect(avatarPathOk('C:\\d\\extensions\\cute\\avatars\\hiyori\\evil.exe', 'cute', 'hiyori')).toBe(false)
    expect(avatarPathOk('C:\\d\\extensions\\cute\\avatars\\other\\x.model3.json', 'cute', 'hiyori')).toBe(false)
    const companion = { motions: {}, lines: { add: {}, replace: {} }, avatars: [{ extension: 'cute', extensionName: '可爱', name: '小艾', file, size: 1 }] }
    expect(findAvatar(companion, 'cute', '小艾')).toBe(file)
    expect(findAvatar(companion, 'cute', '别的')).toBeNull()
    expect(findAvatar(undefined, 'cute', '小艾')).toBeNull()
    expect(validPageName('番茄钟')).toBe(true)
    expect(validPageName('../x')).toBe(false)
    expect(validMotionName('cheer')).toBe(true)
    expect(validMotionName('Cheer')).toBe(false)
  })

  it('目录地址', () => {
    expect(cleanSources([' https://a.com/c.json ', '', 'D:\\c.json'])).toEqual(['https://a.com/c.json', 'D:\\c.json'])
    expect(cleanSources('x')).toBeNull()
    expect(cleanSources(new Array(11).fill('a'))).toBeNull()
  })
})
