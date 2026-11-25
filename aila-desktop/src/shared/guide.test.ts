import { readFileSync } from 'fs'
import { describe, expect, it } from 'vitest'
import { PAGES, SETTINGS_TABS, cleanRoute } from './shell'

// 她查自己功能用的使用说明在 AI 服务那边（aila-ai/app/guide）：主窗口加了页面、设置分区，说明和 open_page 也要跟上
const GUIDE = readFileSync('../aila-ai/app/guide/guide.md', 'utf8')
const GUIDE_PY = readFileSync('../aila-ai/app/guide/__init__.py', 'utf8')

function pyTuple(name: string): string[] {
  const m = GUIDE_PY.match(new RegExp(`^${name} = \\(([^)]*)\\)`, 'm'))
  return m ? [...m[1].matchAll(/"(\w+)"/g)].map((x) => x[1]) : []
}

describe('使用说明', () => {
  it('每一页、每个设置分区都写到了', () => {
    for (const p of PAGES) expect(GUIDE, p.id).toContain(`page=${p.id}`)
    for (const s of SETTINGS_TABS) expect(GUIDE, s.id).toContain(`section=${s.id}`)
  })

  it('open_page 能去的页面、分区和主窗口的一样', () => {
    expect(pyTuple('PAGES')).toEqual(PAGES.map((p) => p.id))
    expect([...pyTuple('SECTIONS')].sort()).toEqual(SETTINGS_TABS.map((s) => s.id).sort())
  })

  it('她发来的页面地址按主窗口的规则检查', () => {
    expect(cleanRoute({ page: 'settings', section: 'voice' })).toEqual({ page: 'settings', section: 'voice' })
    expect(cleanRoute({ page: 'projects', tab: 'map' })).toEqual({ page: 'projects', tab: 'map' })
    expect(cleanRoute({ page: 'evil' })).toEqual({ page: 'tasks' })
  })
})
