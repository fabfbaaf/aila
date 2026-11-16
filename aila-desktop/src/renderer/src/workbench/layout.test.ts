import { describe, expect, it } from 'vitest'
import { FIX_ACTION, FIX_SKILL, pendingFixOf, type CodeGraph, type GraphNode } from '../../../shared/workbench'
import { diffLines } from '../components/bubble'
import { PALETTE, groupAnchors, groupColors, highlightFrom, labelled, linkKey, nodeRadius, searchNodes, starOf } from './layout'

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

  it('每个子项目一个星系中心，彼此分开', () => {
    const anchors = groupAnchors(['ai', 'desktop', 'server'])
    const [a, b] = [anchors.get('ai')!, anchors.get('desktop')!]
    expect(Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2])).toBeGreaterThan(50)
    expect(groupAnchors(['only']).get('only')).toEqual([0, 0, 0])
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
