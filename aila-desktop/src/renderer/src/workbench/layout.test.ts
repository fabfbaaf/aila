import { describe, expect, it } from 'vitest'
import { FIX_ACTION, FIX_SKILL, pendingFixOf, type CodeGraph, type GraphNode } from '../../../shared/workbench'
import { diffLines } from '../components/bubble'
import {
  PALETTE,
  buildModules,
  crossLinks,
  groupAnchors,
  groupColors,
  highlightFrom,
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
    expect(nodeRadius(1)).toBeGreaterThanOrEqual(1.4)
    expect(nodeRadius(100_000)).toBe(9)
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
