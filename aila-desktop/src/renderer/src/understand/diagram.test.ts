import { describe, expect, it } from 'vitest'
import type { AreaView, ChainView } from '../../../shared/understand'
import { BOX_W, GAP_X, edgeKind, edgeLabel, edgeTitle, layersOf, layoutArea, layoutChain, neighbours, orderColumns } from './diagram'

const child = (id: string): AreaView['children'][number] => ({
  id, name: id, kind: 'dir', files: 1, lines: 10, functions: 1, tests: 0, onlyTests: false, langs: {}
})

describe('平面图怎么排', () => {
  it('按连线分列，绕回来的线不影响分列', () => {
    const layer = layersOf(['a', 'b', 'c', 'd'], [
      { source: 'a', target: 'b' }, { source: 'b', target: 'c' }, { source: 'c', target: 'b' }, { source: 'a', target: 'c' }
    ])
    expect(layer.get('a')).toBe(0)
    expect(layer.get('b')).toBe(1)
    expect(layer.get('c')).toBe(2) // 最长路径：a → b → c
    expect(layer.get('d')).toBe(0) // 没连线的放最左
  })

  it('同一列里按左边连着的位置排，少交叉', () => {
    const layer = new Map([['a', 0], ['b', 0], ['x', 1], ['y', 1]])
    const cols = orderColumns(layer, [{ source: 'a', target: 'y' }, { source: 'b', target: 'x' }], ['a', 'b', 'x', 'y'])
    expect(cols).toEqual([['a', 'b'], ['y', 'x']])
  })

  it('一层：外面的那几块也排进去，线从左往右、往回的从下面绕', () => {
    const view: AreaView = {
      path: 'ai', name: 'ai', parent: '', children: [child('ai/app'), child('ai/tests')],
      outside: [{ id: 'desk', name: 'desk', kind: 'outside' }],
      links: [{ source: 'desk', target: 'ai/app', kinds: { http: 3 }, via: ['/agent/sessions', '/chat', '/health'] },
        { source: 'ai/app', target: 'desk', kinds: { call: 1 }, via: [] }],
      entries: [], hubs: [], hidden: 0
    }
    const d = layoutArea(view)
    const at = Object.fromEntries(d.boxes.map((b) => [b.id, b]))
    expect(at['desk'].x).toBeLessThan(at['ai/app'].x)
    expect(at['ai/app'].x - at['desk'].x).toBe(BOX_W + GAP_X)
    const http = d.edges.find((e) => e.source === 'desk')!
    expect(http.back).toBe(false)
    expect(http.path.startsWith('M')).toBe(true)
    expect(d.edges.find((e) => e.source === 'ai/app')!.back).toBe(true)
    expect(edgeLabel(http)).toBe('HTTP 3')
    expect(edgeTitle(http)).toBe('desk → ai/app：HTTP 3\n/agent/sessions\n/chat\n/health')
    expect(edgeKind(http)).toBe('http')
    expect(edgeLabel({ kinds: { call: 4, import: 2 }, via: [] })).toBe('调用 4 · 引用 2')
    expect(d.width).toBeGreaterThan(2 * BOX_W)
  })

  it('方块太多就排成格子：外面的在最左一列，里面的从上到下一列列排', () => {
    const children = Array.from({ length: 30 }, (_, i) => child(`app/f${i}`))
    const view: AreaView = {
      path: 'app', name: 'app', parent: '', children, outside: [{ id: 'desk', name: 'desk', kind: 'outside' }],
      links: [{ source: 'desk', target: 'app/f3', kinds: { http: 1 }, via: [] }], entries: [], hubs: [], hidden: 0
    }
    const d = layoutArea(view)
    const at = Object.fromEntries(d.boxes.map((b) => [b.id, b]))
    expect(at['desk'].x).toBeLessThan(at['app/f0'].x)
    expect(at['app/f1'].x).toBe(at['app/f0'].x) // 同一列往下排
    expect(at['app/f1'].y).toBeGreaterThan(at['app/f0'].y)
    const columns = new Set(d.boxes.map((b) => b.x)).size
    expect(columns).toBeGreaterThan(2)
    expect(columns).toBeLessThan(8)
    expect(d.edges).toHaveLength(1)
  })

  it('链路：按离起点几步分列，往上的在左边', () => {
    const view: ChainView = {
      start: 'b.ts',
      nodes: [
        { id: 'a.vue', name: 'a.vue', layer: -1, start: false },
        { id: 'b.ts', name: 'b.ts', layer: 0, start: true },
        { id: 'c.py', name: 'c.py', layer: 1, start: false }
      ],
      links: [{ source: 'a.vue', target: 'b.ts', kind: 'call', weight: 2, via: [] },
        { source: 'b.ts', target: 'c.py', kind: 'http', weight: 1, via: ['/x'] }]
    }
    const d = layoutChain(view)
    const at = Object.fromEntries(d.boxes.map((b) => [b.id, b]))
    expect(at['a.vue'].x).toBeLessThan(at['b.ts'].x)
    expect(at['b.ts'].x).toBeLessThan(at['c.py'].x)
    expect(at['a.vue'].layer).toBe(-1)
    expect(d.edges).toHaveLength(2)
    expect(layoutChain({ start: 'x', nodes: [], links: [] }).boxes).toEqual([])
  })

  it('选中一个方块时，它和连着它的一起亮', () => {
    const edges = [{ source: 'a', target: 'b' }, { source: 'c', target: 'a' }, { source: 'c', target: 'd' }]
    expect([...neighbours(edges, 'a')].sort()).toEqual(['a', 'b', 'c'])
    expect(neighbours(edges, null).size).toBe(0)
  })
})
