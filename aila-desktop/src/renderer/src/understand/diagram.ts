/**
 * 「看懂项目」的平面图怎么排（纯函数，界面在 UnderstandApp.vue）：从左到右分列，连线是弯曲的线。
 *
 * - 一层（area）：按连线分列（从没人连进来的开始，每顺着连线走一步往右一列；绕回来的线不算，免得排不下去），
 *   同一列里按连着的方块的位置排（减少交叉），外面的那几块（兄弟目录、别的程序）也一样排进去
 * - 链路（chain）：服务端给了每个文件离起点几步（往上是负数），直接按它分列，起点在中间偏左
 * 为什么自己排、不用画图库：方块最多几十个，分层排法一百来行就够，不用多带一个依赖
 */
import type { AreaView, ChainView, LinkKindText } from '../../../shared/understand'

export const BOX_W = 196
export const BOX_H = 58
export const GAP_X = 84
export const GAP_Y = 22
export const PAD = 24

export interface Box {
  id: string
  x: number
  y: number
  w: number
  h: number
  layer: number
}

export interface Edge {
  source: string
  target: string
  path: string
  kinds: Partial<Record<LinkKindText, number>>
  via: string[]
  /** 往回连的（右边连到左边、或者同一列） */
  back: boolean
  /** 线上写字的位置 */
  lx: number
  ly: number
}

export interface Diagram {
  boxes: Box[]
  edges: Edge[]
  width: number
  height: number
}

interface PlainLink {
  source: string
  target: string
  kinds: Partial<Record<LinkKindText, number>>
  via: string[]
}

/** 去掉绕回来的线后，每个点离「没人连进来的点」最远几步（最长路径分层） */
export function layersOf(ids: string[], links: { source: string; target: string; kinds?: Partial<Record<LinkKindText, number>> }[]): Map<string, number> {
  const out = new Map<string, string[]>(ids.map((id) => [id, []]))
  for (const l of links) if (l.source !== l.target && out.has(l.source) && out.has(l.target)) out.get(l.source)!.push(l.target)
  // 深度优先找绕回来的线（指向还在路上的点），排层时不算它们
  const state = new Map<string, 1 | 2>()
  const forward = new Map<string, string[]>(ids.map((id) => [id, []]))
  const visit = (id: string): void => {
    state.set(id, 1)
    for (const next of out.get(id)!) {
      if (state.get(next) === 1) continue // 绕回来的
      forward.get(id)!.push(next)
      if (!state.has(next)) visit(next)
    }
    state.set(id, 2)
  }
  // 从哪些点开始走：没人连进来的先走；成环的时候，往外连得多、被连得少的先走（它更像是「发起方」，放左边）
  const incoming = new Set(links.map((l) => l.target))
  const net = new Map<string, number>(ids.map((id) => [id, 0]))
  for (const l of links) {
    const w = Object.values(l.kinds ?? {}).reduce((a, b) => a + (b ?? 0), 0) || 1
    net.set(l.source, (net.get(l.source) ?? 0) + w)
    net.set(l.target, (net.get(l.target) ?? 0) - w)
  }
  const roots = [...ids].sort((a, b) => Number(incoming.has(a)) - Number(incoming.has(b)) || net.get(b)! - net.get(a)!)
  for (const id of roots) if (!state.has(id)) visit(id)
  // 按拓扑顺序算最长路径
  const indeg = new Map<string, number>(ids.map((id) => [id, 0]))
  for (const targets of forward.values()) for (const t of targets) indeg.set(t, indeg.get(t)! + 1)
  const layer = new Map<string, number>(ids.map((id) => [id, 0]))
  const queue = ids.filter((id) => indeg.get(id) === 0)
  while (queue.length) {
    const id = queue.shift()!
    for (const t of forward.get(id)!) {
      layer.set(t, Math.max(layer.get(t)!, layer.get(id)! + 1))
      indeg.set(t, indeg.get(t)! - 1)
      if (indeg.get(t) === 0) queue.push(t)
    }
  }
  return layer
}

/** 每一列里的先后：先按给的顺序，再按左边连着它的那些方块的平均位置排两遍（减少交叉） */
export function orderColumns(layer: Map<string, number>, links: { source: string; target: string }[], initial: string[]): string[][] {
  const columns: string[][] = []
  for (const id of initial) {
    const l = layer.get(id) ?? 0
    ;(columns[l] ??= []).push(id)
  }
  const cols = columns.map((c) => c ?? [])
  const pos = new Map<string, number>()
  const index = (): void => cols.forEach((c) => c.forEach((id, i) => pos.set(id, i)))
  index()
  for (let pass = 0; pass < 2; pass++) {
    for (let l = 1; l < cols.length; l++) {
      const score = (id: string): number => {
        const left = links.filter((x) => x.target === id && layer.get(x.source) === l - 1).map((x) => pos.get(x.source)!)
        return left.length ? left.reduce((a, b) => a + b, 0) / left.length : pos.get(id)!
      }
      cols[l] = [...cols[l]].sort((a, b) => score(a) - score(b))
      index()
    }
  }
  return cols
}

/**
 * 从 a 连到隔了几列的 b：中间那几列里有方块挡在这条线经过的高度上，就绕开——走这几列所有方块的上面或下面
 * （哪边离两头近走哪边；上面贴边了就走下面）。没挡着返回 null（直接画弯线）
 */
export function aroundBetween(boxes: Box[], a: Box, b: Box, y1: number, y2: number): { left: number; right: number; y: number } | null {
  const between = boxes.filter((x) => x !== a && x !== b && x.x > a.x + a.w && x.x + x.w < b.x)
  if (!between.length) return null
  const lo = Math.min(y1, y2) - 8
  const hi = Math.max(y1, y2) + 8
  if (!between.some((x) => x.y < hi && x.y + x.h > lo)) return null
  const top = Math.min(...between.map((x) => x.y)) - 16
  const bottom = Math.max(...between.map((x) => x.y + x.h)) + 16
  const mid = (y1 + y2) / 2
  const y = top >= 14 && Math.abs(top - mid) <= Math.abs(bottom - mid) ? top : bottom
  return { left: Math.min(...between.map((x) => x.x)) - 12, right: Math.max(...between.map((x) => x.x + x.w)) + 12, y }
}

function place(cols: string[][], firstLayer: number, links: PlainLink[]): Diagram {
  const tallest = Math.max(1, ...cols.map((c) => c.length))
  const boxes: Box[] = []
  const at = new Map<string, Box>()
  cols.forEach((col, l) => {
    const offset = ((tallest - col.length) * (BOX_H + GAP_Y)) / 2 // 每一列竖着居中
    col.forEach((id, i) => {
      const box = { id, x: PAD + l * (BOX_W + GAP_X), y: PAD + offset + i * (BOX_H + GAP_Y), w: BOX_W, h: BOX_H, layer: l + firstLayer }
      boxes.push(box)
      at.set(id, box)
    })
  })
  const edges: Edge[] = []
  for (const link of links) {
    const a = at.get(link.source)
    const b = at.get(link.target)
    if (!a || !b || a === b) continue
    const back = b.x <= a.x
    let path: string
    let lx: number
    let ly: number
    if (!back) {
      const x1 = a.x + a.w, y1 = a.y + a.h / 2, x2 = b.x, y2 = b.y + b.h / 2
      const detour = aroundBetween(boxes, a, b, y1, y2)
      if (detour) {
        // 隔了一列以上、中间的方块挡着：先拐到中间那几列的上面（或下面），横着过去，再拐下来，不从方块后面穿过
        const { left, right, y } = detour
        const g1 = Math.max(20, (left - x1) / 2)
        const g2 = Math.max(20, (x2 - right) / 2)
        path = `M${x1},${y1} C${x1 + g1},${y1} ${left - g1},${y} ${left},${y} L${right},${y} C${right + g2},${y} ${x2 - g2},${y2} ${x2},${y2}`
        lx = (left + right) / 2
        ly = y - 5
      } else {
        const dx = Math.max(30, (x2 - x1) / 2)
        path = `M${x1},${y1} C${x1 + dx},${y1} ${x2 - dx},${y2} ${x2},${y2}`
        lx = (x1 + x2) / 2
        ly = (y1 + y2) / 2 - 6
      }
    } else {
      // 往回连：从下边绕过去，不压在方块上
      const x1 = a.x + a.w / 2, y1 = a.y + a.h, x2 = b.x + b.w / 2, y2 = b.y + b.h
      const drop = Math.max(y1, y2) + 36
      path = `M${x1},${y1} C${x1},${drop} ${x2},${drop} ${x2},${y2}`
      lx = (x1 + x2) / 2
      ly = drop - 8
    }
    edges.push({ source: link.source, target: link.target, path, kinds: link.kinds, via: link.via, back, lx, ly })
  }
  const width = PAD * 2 + Math.max(1, cols.length) * BOX_W + Math.max(0, cols.length - 1) * GAP_X
  const height = PAD * 2 + tallest * BOX_H + Math.max(0, tallest - 1) * GAP_Y + 40
  return { boxes, edges, width, height }
}

/** 方块多过这个数就不按连线分列了（几十个方块分层会排成又高又空的几列），改成排成格子：
 * 外面的那几块一列在最左，里面的按服务端给的顺序（目录在前、大的在前）从上到下、一列列排；线平时淡着，选中才亮 */
export const GRID_AFTER = 16

/** 一层：子目录、文件和外面的那几块 */
export function layoutArea(view: AreaView): Diagram {
  if (view.children.length + view.outside.length > GRID_AFTER) {
    const inner = view.children.map((c) => c.id)
    const rows = Math.max(4, Math.ceil(Math.sqrt(inner.length * 2.5)))
    const cols: string[][] = view.outside.length ? [view.outside.map((o) => o.id)] : []
    for (let i = 0; i < inner.length; i += rows) cols.push(inner.slice(i, i + rows))
    return place(cols, 0, view.links)
  }
  const ids = [...view.children.map((c) => c.id), ...view.outside.map((o) => o.id)]
  const layer = layersOf(ids, view.links)
  const cols = orderColumns(layer, view.links, ids)
  return place(cols, 0, view.links)
}

/** 链路：按服务端给的步数分列 */
export function layoutChain(view: ChainView): Diagram {
  if (!view.nodes.length) return { boxes: [], edges: [], width: 0, height: 0 }
  const min = Math.min(...view.nodes.map((n) => n.layer))
  const layer = new Map(view.nodes.map((n) => [n.id, n.layer - min]))
  const links: PlainLink[] = view.links.map((l) => ({ source: l.source, target: l.target, kinds: { [l.kind]: l.weight }, via: l.via }))
  const cols = orderColumns(layer, links, view.nodes.map((n) => n.id))
  return place(cols, min, links)
}

/** 连线上写的字：种类和处数（「HTTP 119 · 调用 5」），列之间放得下；经过哪些接口放在悬停提示里（edgeTitle） */
const KIND_TEXT: Record<LinkKindText, string> = { http: 'HTTP', ipc: 'IPC', call: '调用', import: '引用' }
const KIND_ORDER: LinkKindText[] = ['http', 'ipc', 'call', 'import']
export function edgeLabel(edge: Pick<Edge, 'kinds' | 'via'>, t: (s: string) => string = (s) => s): string {
  return KIND_ORDER.filter((k) => edge.kinds[k]).map((k) => `${t(KIND_TEXT[k])} ${edge.kinds[k]}`).join(' · ')
}

/** 悬停在线上时的提示：哪到哪、每种几处、经过的接口 */
export function edgeTitle(edge: Pick<Edge, 'source' | 'target' | 'kinds' | 'via'>, t: (s: string) => string = (s) => s): string {
  const head = `${edge.source} → ${edge.target}：${edgeLabel(edge, t)}`
  return edge.via.length ? `${head}\n${edge.via.join('\n')}` : head
}

/** 线的主要种类（决定颜色）：有跨进程的就算跨进程 */
export function edgeKind(edge: Pick<Edge, 'kinds'>): LinkKindText {
  for (const k of ['http', 'ipc', 'call', 'import'] as LinkKindText[]) if (edge.kinds[k]) return k
  return 'call'
}

/** 选中一个方块时要亮的：它和直接连着它的 */
export function neighbours(edges: Pick<Edge, 'source' | 'target'>[], id: string | null): Set<string> {
  const out = new Set<string>()
  if (!id) return out
  out.add(id)
  for (const e of edges) {
    if (e.source === id) out.add(e.target)
    if (e.target === id) out.add(e.source)
  }
  return out
}
