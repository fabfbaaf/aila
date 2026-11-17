import { describe, expect, it } from 'vitest'
import { cleanRoute, parseHash, routeForSection, toHash } from './shell'

describe('主窗口的页面地址', () => {
  it('旧地址照样认', () => {
    expect(parseHash('#/agent')).toEqual({ page: 'tasks' })
    expect(parseHash('#/workbench')).toEqual({ page: 'projects', tab: 'map' })
    expect(parseHash('#/settings/voice')).toEqual({ page: 'settings', section: 'voice' })
    // 资料库、记忆、代码库从设置里搬出来了
    expect(parseHash('#/settings/knowledge')).toEqual({ page: 'knowledge' })
    expect(parseHash('#/settings/memory')).toEqual({ page: 'memory' })
    expect(parseHash('#/settings/code')).toEqual({ page: 'projects', tab: 'list' })
  })

  it('来回转换不变', () => {
    for (const h of ['/tasks', '/chat', '/projects/list', '/projects/map', '/knowledge', '/memory', '/settings/model', '/settings/status', '/settings/agent']) {
      expect(toHash(parseHash(h))).toBe(h)
    }
  })

  it('认不出来的去任务页；设置分区不对就去「对话」', () => {
    expect(parseHash('')).toEqual({ page: 'tasks' })
    expect(parseHash('#/nope')).toEqual({ page: 'tasks' })
    expect(parseHash('#/settings/../../etc')).toEqual({ page: 'settings', section: 'model' })
    expect(routeForSection(undefined)).toEqual({ page: 'settings', section: 'model' })
  })

  it('主进程传来的页面先检查', () => {
    expect(cleanRoute({ page: 'projects', tab: 'map' })).toEqual({ page: 'projects', tab: 'map' })
    expect(cleanRoute({ page: 'settings', section: 'skills' })).toEqual({ page: 'settings', section: 'skills' })
    expect(cleanRoute({ page: 'evil' })).toEqual({ page: 'tasks' })
    expect(cleanRoute('tasks')).toEqual({ page: 'tasks' })
  })
})
