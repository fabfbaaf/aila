import { describe, expect, it } from 'vitest'
import { FIX_ACTION, FIX_SKILL, pendingFixOf, type CodeGraph, type GraphNode } from '../../../shared/workbench'
import { diffLines } from '../components/bubble'
import {
  PALETTE,
  buildModules,
  crossLinks,
  groupAnchors,
  groupColors,
  haloScale,
  highlightFrom,
  evidenceHighlight,
  isRelated,
  layerAlpha,
  relatedFiles,
  relatedModules,
  labelled,
  linkKey,
  moduleAnchors,
  moduleLabels,
  nodeRadius,
  placeLabels,
  searchNodes,
  starOf,
  textWidth,
  withoutTests,
  zoomLevel
} from './layout'

const node = (id: string, group: string, lines = 100): GraphNode => ({
  id,
  name: id.split('/').pop()!,
  group,
  lang: 'python',
  lines,
  functions: 3,
  modified: false
})

const GRAPH: CodeGraph = {
  nodes: [
    node('ai/app/agent.py', 'ai', 400),
    node('ai/app/tools/code.py', 'ai', 250),
    node('ai/app/routes/chat.py', 'ai', 120),
    node('desktop/src/App.vue', 'desktop', 600),
    node('server/Sync.java', 'server', 300)
  ],
  links: [
    { source: 'ai/app/routes/chat.py', target: 'ai/app/agent.py', weight: 2 },
    { source: 'ai/app/agent.py', target: 'ai/app/tools/code.py', weight: 1 },
    { source: 'desktop/src/App.vue', target: 'ai/app/agent.py', weight: 1 }
  ]
}

describe('星图布局', () => {
  it('最大的子项目用第一个颜色，颜色不重复', () => {
    const colors = groupColors(GRAPH.nodes)
    expect(colors.get('ai')).toBe(PALETTE[0])
    expect(new Set(colors.values()).size).toBe(3)
  })

  it('星球大小跟着行数变，但有上下限', () => {
    expect(nodeRadius(1)).toBeGreaterThanOrEqual(1.2)
    expect(nodeRadius(100_000)).toBe(6)
    expect(nodeRadius(800)).toBeGreaterThan(nodeRadius(100))
  })

  it('每个子项目一个星系中心，彼此分开；从正面看也不会前后挡住', () => {
    const anchors = groupAnchors(['ai', 'desktop', 'server'])
    const list = [...anchors.values()]
    for (let i = 0; i < list.length; i++) {
      for (let j = i + 1; j < list.length; j++) {
        const [a, b] = [list[i], list[j]]
        expect(Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2])).toBeGreaterThan(50)
        expect(Math.hypot(a[0] - b[0], a[1] - b[1])).toBeGreaterThan(80) // 投到屏幕上（镜头在正前方）也分得开
      }
    }
    expect(groupAnchors(['only']).get('only')).toEqual([0, 0, 0])
  })

  it('模块的名字：去掉子项目、src、Java 的包前缀', () => {
    const labels = moduleLabels([
      { id: 'aila-desktop/src/renderer/src/agent', group: 'aila-desktop' },
      { id: 'aila-desktop/src/main', group: 'aila-desktop' },
      { id: 'aila-desktop', group: 'aila-desktop' },
      { id: 'srv/src/main/java/com/x/app', group: 'srv' },
      { id: 'srv/src/main/java/com/x/app/auth', group: 'srv' },
      { id: 'srv/src/test/java/com/x/app', group: 'srv' }
    ])
    expect(labels.get('aila-desktop/src/renderer/src/agent')).toBe('renderer/agent')
    expect(labels.get('aila-desktop/src/main')).toBe('main')
    expect(labels.get('aila-desktop')).toBe('aila-desktop') // 子项目根目录下的文件
    expect(labels.get('srv/src/main/java/com/x/app/auth')).toBe('auth')
    expect(labels.get('srv/src/main/java/com/x/app')).toBe('app')
    expect(labels.get('srv/src/test/java/com/x/app')).toBe('test/app')
  })

  it('按目录聚成模块；模块之间的光线按种类加起来，模块里面的不算', () => {
    const graph: CodeGraph = {
      nodes: [...GRAPH.nodes, { ...node('ai/tests/test_agent.py', 'ai'), test: true }],
      links: [
        ...GRAPH.links,
        { source: 'desktop/src/App.vue', target: 'ai/app/routes/chat.py', weight: 2, kind: 'http', via: ['/chat'] },
        { source: 'ai/tests/test_agent.py', target: 'ai/app/agent.py', weight: 1 }
      ]
    }
    const { modules, links } = buildModules(graph)
    const byId = new Map(modules.map((m) => [m.id, m]))
    expect(byId.get('ai/app')).toMatchObject({ files: ['ai/app/agent.py'], lines: 400, test: false })
    expect(byId.get('ai/tests')?.test).toBe(true)
    expect(links).toContainEqual({ source: 'desktop/src', target: 'ai/app/routes', kind: 'http', weight: 2 })
    expect(links).toContainEqual({ source: 'desktop/src', target: 'ai/app', kind: 'call', weight: 1 })
    expect(links.every((l) => l.source !== l.target)).toBe(true)
    // 模块按大小撒在星系周围：最大的在中心，彼此不重合
    const anchors = moduleAnchors(modules, groupAnchors(['ai', 'desktop', 'server']))
    const ai = [...anchors.entries()].filter(([id]) => id.startsWith('ai/')).map(([, p]) => p.join(','))
    expect(new Set(ai).size).toBe(ai.length)
    expect(anchors.size).toBe(modules.length)
  })

  it('跨进程的光线和函数调用分开记，选中文件时列出来', () => {
    const http = { source: 'desktop/src/App.vue', target: 'ai/app/routes/chat.py', weight: 1, kind: 'http' as const }
    expect(linkKey(http)).not.toBe(linkKey({ ...http, kind: 'call' }))
    expect(linkKey({ source: 'a', target: 'b' })).toBe(linkKey({ source: 'a', target: 'b', kind: 'call' }))
    const g = { ...GRAPH, links: [...GRAPH.links, http] }
    expect(crossLinks(g, 'desktop/src/App.vue')).toEqual({ out: [http], in: [] })
    expect(crossLinks(g, 'ai/app/routes/chat.py').in).toEqual([http])
  })

  it('隐藏测试文件：连着它们的光线一起去掉', () => {
    const g: CodeGraph = {
      nodes: [...GRAPH.nodes, { ...node('ai/tests/test_x.py', 'ai'), test: true }],
      links: [...GRAPH.links, { source: 'ai/tests/test_x.py', target: 'ai/app/agent.py', weight: 1 }]
    }
    const shown = withoutTests(g)
    expect(shown.nodes.map((n) => n.id)).not.toContain('ai/tests/test_x.py')
    expect(shown.links).toEqual(GRAPH.links)
  })

  it('拉远看模块、拉近看文件：看全部时是模块，拉近一半是文件，中间平滑过渡', () => {
    expect(zoomLevel(300, 300)).toBe(1)
    expect(zoomLevel(120, 300)).toBe(0)
    const mid = zoomLevel(190, 300)
    expect(mid).toBeGreaterThan(0)
    expect(mid).toBeLessThan(1)
    expect(zoomLevel(200, 300)).toBeGreaterThan(mid)
  })

  it('名字防重叠：优先级高的先摆，挡住的不显示', () => {
    const shown = placeLabels([
      { id: 'small', x: 100, y: 100, w: 60, h: 18, priority: 1 },
      { id: 'big', x: 110, y: 104, w: 80, h: 18, priority: 9 },
      { id: 'far', x: 400, y: 100, w: 60, h: 18, priority: 0 }
    ])
    expect(shown).toEqual(new Set(['big', 'far']))
    expect(textWidth('ab', 10)).toBeLessThan(textWidth('模块', 10))
  })

  it('报错：出错的文件标红，调用链上的标橙，只亮它们之间的光线', () => {
    const h = highlightFrom(
      {
        project: 'p',
        name: 'p',
        frames: [
          { file: 'ai/app/tools/code.py', line: 40, qname: 'locate' },
          { file: null, line: 3, qname: null }
        ],
        chain: [
          { file: 'ai/app/agent.py', qname: 'run_agent', line: 31 },
          { file: 'ai/app/routes/chat.py', qname: 'chat', line: 104 }
        ]
      },
      GRAPH
    )
    expect([...h.error]).toEqual(['ai/app/tools/code.py'])
    expect([...h.chain].sort()).toEqual(['ai/app/agent.py', 'ai/app/routes/chat.py'])
    expect(h.links).toEqual(
      new Set([
        linkKey({ source: 'ai/app/routes/chat.py', target: 'ai/app/agent.py' }),
        linkKey({ source: 'ai/app/agent.py', target: 'ai/app/tools/code.py' })
      ])
    )
    expect(h.primary).toEqual({ file: 'ai/app/tools/code.py', line: 40, qname: 'locate' })
  })

  it('搜文件：名字开头对上的在前', () => {
    expect(searchNodes(GRAPH.nodes, 'code').map((n) => n.id)).toEqual(['ai/app/tools/code.py'])
    expect(searchNodes(GRAPH.nodes, 'a')[0].name.startsWith('a')).toBe(true)
    expect(searchNodes(GRAPH.nodes, '  ')).toEqual([])
  })

  it('默认只给最大的几个星球标名字', () => {
    expect(labelled(GRAPH.nodes, 2)).toEqual(new Set(['desktop/src/App.vue', 'ai/app/agent.py']))
  })
})

describe('星图里显示的待确认修改', () => {
  const ask = { id: 'c1', skill: FIX_SKILL, action: FIX_ACTION, detail: '项目：shop\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b' }

  it('只有修代码的确认会显示到星图里（双手合十只能确认它）', () => {
    expect(pendingFixOf(ask)).toEqual({ id: 'c1', detail: ask.detail })
    expect(pendingFixOf({ ...ask, skill: '浏览器', action: '提交表单' })).toBeNull()
    expect(pendingFixOf(null)).toBeNull()
  })

  it('diff 按行标出种类', () => {
    expect(diffLines(ask.detail).map((l) => l.kind)).toEqual(['', 'file', 'file', 'hunk', 'del', 'add'])
  })
})

describe('她干活的文件对到星球上', () => {
  it('会话目录可以是项目根目录或者子目录；不分大小写和斜杠方向；项目外的文件不算', () => {
    expect(starOf('C:\\code\\shop', 'C:\\code\\shop', 'src/cart.py')).toBe('src/cart.py')
    expect(starOf('C:\\code\\shop', 'c:/code/shop/backend', './api/x.py')).toBe('backend/api/x.py')
    expect(starOf('C:\\code\\shop', 'C:\\code\\blog', 'a.py')).toBeNull()
    expect(starOf('C:\\code\\shop', 'C:\\code\\shopping', 'a.py')).toBeNull()
  })
})

describe('星图的分层显示', () => {
  it('看全部时只有模块和模块之间的线，文件之间的线、光点都不画', () => {
    const far = layerAlpha(1)
    expect(far.fileLines).toBe(0)
    expect(far.sparks).toBe(0)
    expect(far.moduleSuns).toBeGreaterThan(0.5)
    expect(far.moduleLines).toBeGreaterThan(0.3)
    expect(far.planets).toBeLessThan(0.4)
  })

  it('拉近看文件：模块的大星和星云都没了，文件、文件之间的线出来', () => {
    const near = layerAlpha(0)
    expect(near.moduleSuns).toBe(0)
    expect(near.nebula).toBe(0)
    expect(near.planets).toBe(1)
    expect(near.fileLines).toBeGreaterThan(0)
    expect(near.sparks).toBeGreaterThan(0)
  })

  it('光晕、星云不会糊成一片：都压得很低；跨进程弧线一直很淡', () => {
    for (const z of [0, 0.25, 0.5, 0.75, 1]) {
      const a = layerAlpha(z)
      expect(a.halos).toBeLessThanOrEqual(0.55)
      expect(a.nebula).toBeLessThanOrEqual(0.05)
      expect(a.arcs).toBeLessThanOrEqual(0.3)
    }
    expect(haloScale(0)).toBeLessThan(haloScale(1))
  })

  it('越拉近文件之间的线越清楚（不会突然跳）', () => {
    const steps = [1, 0.8, 0.6, 0.4, 0.2, 0].map((z) => layerAlpha(z).fileLines)
    for (let i = 1; i < steps.length; i++) expect(steps[i]).toBeGreaterThanOrEqual(steps[i - 1])
  })

  it('名字不压在工具栏、图例上', () => {
    const toolbar = { id: 'ui:top', x: 300, y: 30, w: 600, h: 40, priority: 0 }
    const shown = placeLabels(
      [
        { id: 'under', x: 320, y: 32, w: 80, h: 18, priority: 9000 },
        { id: 'free', x: 320, y: 300, w: 80, h: 18, priority: 1 }
      ],
      3,
      [toolbar]
    )
    expect(shown.has('under')).toBe(false)
    expect(shown.has('free')).toBe(true)
    expect(shown.has('ui:top')).toBe(false)
  })
})

describe('关联高亮', () => {
  const links = [
    { source: 'a.ts', target: 'b.ts', kind: 'call' as const },
    { source: 'a.ts', target: 'c.ts', kind: 'import' as const },
    { source: 'd.ts', target: 'a.ts', kind: 'call' as const },
    { source: 'e.ts', target: 'a.ts', kind: 'http' as const },
    { source: 'b.ts', target: 'c.ts', kind: 'call' as const },
    { source: 'a.ts', target: 'a.ts', kind: 'call' as const }
  ]

  it('它用到的、用到它的分开记，连着它的每条线记方向；只看直接相连的一层', () => {
    const r = relatedFiles(links, 'a.ts')
    expect([...r.out].sort()).toEqual(['b.ts', 'c.ts'])
    expect([...r.in].sort()).toEqual(['d.ts', 'e.ts'])
    expect(r.links.get('a.ts→b.ts:call') ?? r.links.get([...r.links.keys()].find((k) => k.includes('b.ts')) ?? '')).toBe('out')
    expect([...r.links.values()].filter((v) => v === 'in')).toHaveLength(2)
    // b → c 不连着 a：不算
    expect(r.links.size).toBe(4)
    expect(isRelated(r, 'a.ts') && isRelated(r, 'd.ts')).toBe(true)
    expect(isRelated(r, 'f.ts')).toBe(false)
  })

  it('没有关联的文件：只有它自己', () => {
    const r = relatedFiles(links, 'zzz.ts')
    expect(r.out.size + r.in.size + r.links.size).toBe(0)
    expect(isRelated(r, 'zzz.ts')).toBe(true)
  })

  it('模块之间也分方向', () => {
    const m = relatedModules([{ source: 'ui', target: 'api' }, { source: 'cli', target: 'ui' }, { source: 'api', target: 'db' }], 'ui')
    expect([...m.out]).toEqual(['api'])
    expect([...m.in]).toEqual(['cli'])
  })
})


it('星图只高亮找到的证据文件和检索经过的边', () => {
  const highlight = evidenceHighlight({
    locations: [
      { file: 'gone.py', line: 1 },
      { file: 'ai/app/routes/chat.py', line: 17 },
      { file: 'ai/app/agent.py', line: 28 },
      { file: 'ai/app/tools/code.py', line: 39 }
    ],
    edges: [['ai/app/agent.py', 'ai/app/routes/chat.py'], ['gone.py', 'ai/app/agent.py']]
  }, GRAPH)
  expect(highlight.primary).toEqual({ file: 'ai/app/routes/chat.py', line: 17, qname: null })
  expect([...highlight.chain]).toHaveLength(3)
  expect(highlight.error.size).toBe(0)
  expect([...highlight.links]).toEqual([linkKey(GRAPH.links[0])])
})
