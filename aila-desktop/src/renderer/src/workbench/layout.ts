// 星图里不涉及 3D 的部分：颜色、大小、分组和模块的位置、模块的名字、名字防重叠、远近切换、搜索、报错时哪些星球和光线要亮
import type { CodeGraph, GraphLink, GraphNode, LinkKind, LocatedError } from '../../../shared/workbench'

/** 子项目的颜色：亮、彼此分得开（报错用的红色不在里面） */
export const PALETTE = ['#5ad1ff', '#b58cff', '#ffd35a', '#5affb4', '#ff8fd1', '#8fa8ff', '#ffa95a', '#7dffef']
export const ERROR_COLOR = '#ff3b4a'
export const CHAIN_COLOR = '#ff9a3c'
/** 她这次改过的文件 */
export const TOUCH_COLOR = '#4ade80'
/** 她正在读的文件闪一下 */
export const READ_COLOR = '#67e8f9'
/** 跨进程的光线：淡金是 HTTP 接口，淡青是 IPC 消息（和子项目的颜色分得开：都很淡、画成弧线） */
export const HTTP_COLOR = '#ffe9a8'
export const IPC_COLOR = '#b8f0ff'

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

export type Vec3 = [number, number, number]

/**
 * 每个子项目一个「星系」中心。排在正对镜头的平面上（只带一点纵深），
 * 从默认角度看几个星系不会叠在一起（以前排在水平的圆上，三个子项目时有两个正好前后挡住）
 */
export function groupAnchors(groups: string[], sizes: number[] = []): Map<string, Vec3> {
  if (groups.length <= 1) return new Map(groups.map((g) => [g, [0, 0, 0]]))
  const biggest = Math.max(1, ...sizes)
  const radius = 90 + 40 * groups.length + 9 * Math.sqrt(biggest)
  return new Map(
    groups.map((g, i) => {
      const a = Math.PI + (2 * Math.PI * i) / groups.length // 第一个（最大的）在左边
      return [g, [Math.cos(a) * radius, Math.sin(a) * radius * 0.8, Math.sin(a) * radius * 0.15]]
    })
  )
}

/** 文件在哪个模块（目录）；旧服务没给就按路径算 */
export function moduleOf(n: Pick<GraphNode, 'id' | 'module'>): string {
  return n.module ?? (n.id.includes('/') ? n.id.slice(0, n.id.lastIndexOf('/')) : '.')
}

export function kindOf(l: Pick<GraphLink, 'kind'>): LinkKind {
  return l.kind ?? 'call'
}

export function linkKey(l: Pick<GraphLink, 'source' | 'target' | 'kind'>): string {
  const kind = kindOf(l)
  return kind === 'call' ? `${l.source}→${l.target}` : `${kind}:${l.source}→${l.target}`
}

/** 一个模块（目录）：拉远时缩成一颗带名字的大星 */
export interface ModuleInfo {
  id: string
  group: string
  label: string
  files: string[]
  lines: number
  /** 里面全是测试文件 */
  test: boolean
}

/** 模块之间的光线：里面文件之间的光线按种类加起来 */
export interface ModuleLink {
  source: string
  target: string
  kind: LinkKind
  weight: number
}

/**
 * 模块的名字：去掉子项目那一段、Java 的 src/main/java、到处都有的 src，再去掉同一个子项目里大家都有的开头。
 * aila-desktop/src/renderer/src/agent → renderer/agent；server/src/main/java/com/x/app/auth → auth
 */
export function moduleLabels(modules: { id: string; group: string }[]): Map<string, string> {
  const strip = (id: string, group: string): string[] => {
    let rest = id === group ? '' : id.startsWith(`${group}/`) ? id.slice(group.length + 1) : id
    rest = rest.replace(/(^|\/)src\/main\/(java|kotlin|resources)(\/|$)/, '$1').replace(/(^|\/)src\/test\/(java|kotlin)(\/|$)/, '$1test/')
    return rest.split('/').filter((p) => p && p !== 'src' && p !== '.')
  }
  const byGroup = new Map<string, { id: string; parts: string[] }[]>()
  for (const m of modules) {
    const list = byGroup.get(m.group) ?? []
    list.push({ id: m.id, parts: strip(m.id, m.group) })
    byGroup.set(m.group, list)
  }
  const out = new Map<string, string>()
  for (const [group, list] of byGroup) {
    // 大家都有的开头（com/aila/server）：去掉；测试目录（test/com/aila/server）前面的 test 留着、不参与比较
    const core = list.map((m) => ({ ...m, test: m.parts[0] === 'test', rest: m.parts[0] === 'test' ? m.parts.slice(1) : m.parts }))
    let common = 0
    if (list.length > 1) {
      const first = core[0].rest
      while (common < first.length && core.every((m) => m.rest.length > common && m.rest[common] === first[common])) common++
    }
    for (const m of core) {
      const rest = m.rest.slice(common)
      // 剩不下了（就是那个公共的包）：用公共开头的最后一段
      // 太深的只留最后两段（eval/tasks/fix-x/repo → fix-x/repo）
      const name = rest.length ? rest.slice(-2).join('/') : common ? m.rest[common - 1] : ''
      out.set(m.id, [m.test ? 'test' : '', name].filter(Boolean).join('/') || group)
    }
  }
  return out
}

/** 按目录聚成模块，模块之间的光线（同一个模块里的不算） */
export function buildModules(graph: CodeGraph): { modules: ModuleInfo[]; links: ModuleLink[] } {
  const map = new Map<string, ModuleInfo>()
  const owner = new Map<string, string>()
  for (const n of graph.nodes) {
    const id = moduleOf(n)
    owner.set(n.id, id)
    const m = map.get(id) ?? { id, group: n.group, label: id, files: [], lines: 0, test: true }
    m.files.push(n.id)
    m.lines += n.lines
    m.test = m.test && !!n.test
    map.set(id, m)
  }
  const labels = moduleLabels([...map.values()])
  for (const m of map.values()) m.label = labels.get(m.id) ?? m.id
  const links = new Map<string, ModuleLink>()
  for (const l of graph.links) {
    const a = owner.get(l.source)
    const b = owner.get(l.target)
    if (!a || !b || a === b) continue
    const kind = kindOf(l)
    const key = `${kind}:${a}→${b}`
    const cur = links.get(key) ?? { source: a, target: b, kind, weight: 0 }
    cur.weight += l.weight
    links.set(key, cur)
  }
  return { modules: [...map.values()], links: [...links.values()] }
}

/**
 * 模块在星系里的位置：最大的在星系中心，其余的按大小均匀撒在一个球面上（斐波那契球面），
 * 文件被拉向自己模块的中心，一个目录就聚成一团「星座」
 */
export function moduleAnchors(modules: ModuleInfo[], groups: Map<string, Vec3>): Map<string, Vec3> {
  const out = new Map<string, Vec3>()
  const byGroup = new Map<string, ModuleInfo[]>()
  for (const m of modules) byGroup.set(m.group, [...(byGroup.get(m.group) ?? []), m])
  for (const [group, list] of byGroup) {
    const [gx, gy, gz] = groups.get(group) ?? [0, 0, 0]
    const sorted = [...list].sort((a, b) => b.files.length - a.files.length || a.id.localeCompare(b.id))
    const shell = sorted.slice(1)
    const radius = 14 + 11 * Math.sqrt(sorted.length)
    out.set(sorted[0].id, [gx, gy, gz])
    shell.forEach((m, i) => {
      const y = 1 - (2 * (i + 0.5)) / shell.length
      const r = Math.sqrt(1 - y * y)
      const a = i * 2.39996323 // 黄金角
      out.set(m.id, [gx + Math.cos(a) * r * radius, gy + y * radius * 0.8, gz + Math.sin(a) * r * radius])
    })
  }
  return out
}

/** 模块大星的半径：按总行数的立方根，比文件星球大一圈 */
export function moduleRadius(lines: number): number {
  return Math.min(16, Math.max(3, 2 + Math.cbrt(Math.max(1, lines)) * 0.42))
}

/**
 * 远近：镜头离中心的距离和「看全部」时的距离之比 → 0（近，看文件）到 1（远，看模块）之间平滑过渡。
 * 看全部时是 1：一打开先看到模块结构；拉近到一半左右就是一个个文件
 */
export function zoomLevel(distance: number, fitDistance: number): number {
  const r = distance / Math.max(1, fitDistance)
  const t = Math.min(1, Math.max(0, (r - 0.42) / (0.85 - 0.42)))
  return t * t * (3 - 2 * t)
}

/** 屏幕上的一个名字：中心点、宽高（像素），优先级高的先摆 */
export interface LabelBox {
  id: string
  x: number
  y: number
  w: number
  h: number
  priority: number
}

/** 名字防重叠：按优先级从高到低摆，和已经摆上的挡住了就不显示 */
export function placeLabels(items: LabelBox[], gap = 3): Set<string> {
  const placed: LabelBox[] = []
  const shown = new Set<string>()
  for (const b of [...items].sort((a, c) => c.priority - a.priority)) {
    const hit = placed.some((p) => Math.abs(p.x - b.x) * 2 < p.w + b.w + gap * 2 && Math.abs(p.y - b.y) * 2 < p.h + b.h + gap * 2)
    if (hit) continue
    placed.push(b)
    shown.add(b.id)
  }
  return shown
}

/** 估一个名字在屏幕上有多宽（不量 DOM：每帧量会很慢）：中文按字号，英文按半个字号 */
export function textWidth(text: string, size: number): number {
  let w = 0
  for (const ch of text) w += ch.charCodeAt(0) > 0x2e80 ? size : size * 0.56
  return w
}

/** 隐藏测试文件：去掉测试文件和连着它们的光线 */
export function withoutTests(graph: CodeGraph): CodeGraph {
  const nodes = graph.nodes.filter((n) => !n.test)
  const keep = new Set(nodes.map((n) => n.id))
  return { nodes, links: graph.links.filter((l) => keep.has(l.source) && keep.has(l.target)) }
}

/** 选中文件的跨进程光线：它请求了谁（out）、谁请求了它（in），面板里列出来 */
export function crossLinks(graph: CodeGraph, id: string): { out: GraphLink[]; in: GraphLink[] } {
  const cross = graph.links.filter((l) => kindOf(l) !== 'call')
  return { out: cross.filter((l) => l.source === id), in: cross.filter((l) => l.target === id) }
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

// ---------- 换颜色看项目：按子项目 / 按改动 / 按有没有测试 ----------

export type ColorMode = 'group' | 'churn' | 'tests'

export const TESTED_COLOR = '#4ade80'
export const UNTESTED_COLOR = '#ff7a59'
export const TEST_FILE_COLOR = '#5d6782'
export const COLD_COLOR = '#2f3b58'

/** 改动次数 → 颜色：没改过是暗蓝灰；改得越多从青到黄到红（按对数算，少数热点不会把别的都压成一个色） */
export function churnColor(count: number, max: number): string {
  if (count <= 0 || max <= 0) return COLD_COLOR
  const t = Math.min(1, Math.log(1 + count) / Math.log(1 + max))
  const hue = 190 - 190 * t // 190 青 → 50 黄 → 0 红
  const light = 55 + 5 * t
  return hslHex(hue, 90, light)
}

function hslHex(h: number, s: number, l: number): string {
  const a = (s / 100) * Math.min(l / 100, 1 - l / 100)
  const f = (n: number): string => {
    const k = (n + h / 30) % 12
    const c = l / 100 - a * Math.max(-1, Math.min(k - 3, 9 - k, 1))
    return Math.round(255 * c).toString(16).padStart(2, '0')
  }
  return `#${f(0)}${f(8)}${f(4)}`
}

/** 测试文件对应的被测文件名：test_cart.py、cart_test.py、cart.test.ts、cart.spec.js、CartTest.java → cart / Cart */
export function testedStem(name: string): string | null {
  const base = name.replace(/\.[^.]+$/, '')
  const m =
    /^test_(.+)$/.exec(base) ?? /^(.+)_test$/.exec(base) ?? /^(.+)\.(test|spec)$/.exec(base) ?? /^(.+?)Tests?$/.exec(base)
  return m ? m[1].toLowerCase() : null
}

/**
 * 有测试的文件：测试文件调用了它（任何一种光线从测试文件连过来），或者有同名的测试文件（test_cart.py ↔ cart.py）。
 * 只是估算（没有跑覆盖率），但不用跑测试、一打开就有
 */
export function testedFiles(graph: CodeGraph): Set<string> {
  const tests = new Set(graph.nodes.filter((n) => n.test).map((n) => n.id))
  const out = new Set<string>()
  for (const l of graph.links) if (tests.has(l.source) && !tests.has(l.target)) out.add(l.target)
  const stems = new Set(graph.nodes.filter((n) => n.test).map((n) => testedStem(n.name)).filter((s): s is string => !!s))
  for (const n of graph.nodes) {
    if (!n.test && stems.has(n.name.replace(/\.[^.]+$/, '').toLowerCase())) out.add(n.id)
  }
  return out
}

/** 某种上色方式下每个文件、每个模块的颜色；按子项目（原来的样子）返回 null */
export function modeColors(graph: CodeGraph, mode: ColorMode): { files: Map<string, string>; modules: Map<string, string> } | null {
  if (mode === 'group') return null
  const files = new Map<string, string>()
  const modules = new Map<string, string>()
  const byModule = new Map<string, GraphNode[]>()
  for (const n of graph.nodes) byModule.set(moduleOf(n), [...(byModule.get(moduleOf(n)) ?? []), n])
  if (mode === 'churn') {
    const max = Math.max(0, ...graph.nodes.map((n) => n.churn ?? 0))
    for (const n of graph.nodes) files.set(n.id, churnColor(n.churn ?? 0, max))
    for (const [m, list] of byModule) modules.set(m, churnColor(Math.max(0, ...list.map((n) => n.churn ?? 0)), max))
  } else {
    const tested = testedFiles(graph)
    for (const n of graph.nodes) files.set(n.id, n.test ? TEST_FILE_COLOR : tested.has(n.id) ? TESTED_COLOR : UNTESTED_COLOR)
    for (const [m, list] of byModule) {
      const code = list.filter((n) => !n.test)
      if (!code.length) {
        modules.set(m, TEST_FILE_COLOR)
        continue
      }
      // 模块里有测试的占几成：全有是绿，全没有是橙红，中间渐变
      const share = code.filter((n) => tested.has(n.id)).length / code.length
      modules.set(m, hslHex(14 + (142 - 14) * share, 85, 58))
    }
  }
  return { files, modules }
}

/** 「按改动」时改得最多的几个文件（图例里列出来） */
export function hottest(graph: CodeGraph, count = 5): GraphNode[] {
  return graph.nodes.filter((n) => (n.churn ?? 0) > 0).sort((a, b) => (b.churn ?? 0) - (a.churn ?? 0)).slice(0, count)
}
