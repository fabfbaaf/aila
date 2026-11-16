// 星图里不涉及 3D 的部分：颜色、大小、分组的位置、搜索、报错时哪些星球和光线要亮
import type { CodeGraph, GraphLink, GraphNode, LocatedError } from '../../../shared/workbench'

/** 子项目的颜色：亮、彼此分得开（报错用的红色不在里面） */
export const PALETTE = ['#5ad1ff', '#b58cff', '#ffd35a', '#5affb4', '#ff8fd1', '#8fa8ff', '#ffa95a', '#7dffef']
export const ERROR_COLOR = '#ff3b4a'
export const CHAIN_COLOR = '#ff9a3c'
/** 她这次改过的文件 */
export const TOUCH_COLOR = '#4ade80'
/** 她正在读的文件闪一下 */
export const READ_COLOR = '#67e8f9'

/**
 * 她干活的文件（相对会话目录）→ 星图里的星球 id（相对项目根目录）。不在这个项目里返回 null。
 * Windows 上路径不分大小写，斜杠方向也不一定
 */
export function starOf(root: string, cwd: string, file: string): string | null {
  const norm = (p: string): string => p.replace(/\\/g, '/').replace(/\/+$/, '')
  const r = norm(root)
  const abs = `${norm(cwd)}/${norm(file).replace(/^\.\//, '')}`
  if (!abs.toLowerCase().startsWith(`${r.toLowerCase()}/`)) return null
  return abs.slice(r.length + 1)
}

/** 按每组的文件数排，最大的组用第一个颜色 */
export function groupColors(nodes: GraphNode[]): Map<string, string> {
  const counts = new Map<string, number>()
  for (const n of nodes) counts.set(n.group, (counts.get(n.group) ?? 0) + 1)
  const groups = [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).map(([g]) => g)
  return new Map(groups.map((g, i) => [g, PALETTE[i % PALETTE.length]]))
}

/** 星球半径：按行数的立方根（体积和代码量成正比），夹在一个范围里 */
export function nodeRadius(lines: number): number {
  return Math.min(9, Math.max(1.4, 0.9 + Math.cbrt(Math.max(1, lines)) * 0.55))
}

/** 每个子项目一个「星系」中心：排在一个圆上，同一组的星球被拉向自己的中心 */
export function groupAnchors(groups: string[]): Map<string, [number, number, number]> {
  if (groups.length <= 1) return new Map(groups.map((g) => [g, [0, 0, 0]]))
  const radius = 45 + 18 * groups.length
  return new Map(
    groups.map((g, i) => {
      const a = (2 * Math.PI * i) / groups.length
      return [g, [Math.cos(a) * radius, Math.sin(a) * radius * 0.35, Math.sin(a) * radius]]
    })
  )
}

export function linkKey(l: Pick<GraphLink, 'source' | 'target'>): string {
  return `${l.source}→${l.target}`
}

/** 报错时要亮的：出错的文件（红）、调用链上的文件（橙）、它们之间的光线 */
export interface Highlight {
  error: Set<string>
  chain: Set<string>
  links: Set<string>
  /** 离出错点最近的那一处 */
  primary: { file: string; line: number; qname: string | null } | null
}

export function highlightFrom(loc: LocatedError, graph: CodeGraph): Highlight {
  const error = new Set(loc.frames.map((f) => f.file).filter((f): f is string => !!f))
  const chain = new Set(loc.chain.map((c) => c.file).filter((f) => !error.has(f)))
  const lit = new Set([...error, ...chain])
  const links = new Set(graph.links.filter((l) => lit.has(l.source) && lit.has(l.target)).map(linkKey))
  const first = loc.frames.find((f) => f.file)
  return { error, chain, links, primary: first?.file ? { file: first.file, line: first.line, qname: first.qname } : null }
}

/** 搜文件：名字开头对上的排前面，其次路径短的 */
export function searchNodes(nodes: GraphNode[], query: string, limit = 8): GraphNode[] {
  const q = query.trim().toLowerCase()
  if (!q) return []
  return nodes
    .filter((n) => n.id.toLowerCase().includes(q))
    .sort((a, b) => {
      const sa = a.name.toLowerCase().startsWith(q) ? 0 : 1
      const sb = b.name.toLowerCase().startsWith(q) ? 0 : 1
      return sa - sb || a.id.length - b.id.length || a.id.localeCompare(b.id)
    })
    .slice(0, limit)
}

/** 默认显示名字的星球：最大的几个（太多会挤成一团） */
export function labelled(nodes: GraphNode[], count = 12): Set<string> {
  return new Set([...nodes].sort((a, b) => b.lines - a.lines).slice(0, count).map((n) => n.id))
}
