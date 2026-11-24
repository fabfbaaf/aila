/**
 * 让她在屏幕上走动：认「去右上角」「往左一点」这类话，算出她该走到哪，走的路线怎么缓。
 *
 * 为什么这样设计：
 * - 不用看懂屏幕：位置都是相对她所在那块屏幕的工作区（不含任务栏）说的，左上、右下、中间……
 * - 两条路进来：整句就是一个走动指令时（「去右上角」「往左点」）桌面端当场就走，不等大模型；
 *   说得更随意的（「你挡着我了，躲开点」）由大模型在回复里加 [go-left] 这样的标签（名字和 aila-ai/app/persona.py 的 SPOTS 一样）
 * - 认指令只认「整句就是这个意思」：「我右下角有个按钮点不了」不会让她跑过去
 * - 都是纯函数，主进程只管按算好的位置挪窗口
 */

export type Spot = 'topleft' | 'top' | 'topright' | 'left' | 'center' | 'right' | 'bottomleft' | 'bottom' | 'bottomright'
export type Nudge = 'nudge-left' | 'nudge-right' | 'nudge-up' | 'nudge-down'
/** stroll：没事时自己走两步（设置里「没事时走走」），方向和远近由 strollTarget 定 */
export type MoveTarget = Spot | Nudge | 'stroll'

export const SPOTS: readonly Spot[] = ['topleft', 'top', 'topright', 'left', 'center', 'right', 'bottomleft', 'bottom', 'bottomright']
const NUDGES: readonly Nudge[] = ['nudge-left', 'nudge-right', 'nudge-up', 'nudge-down']

export function isMoveTarget(v: unknown): v is MoveTarget {
  return typeof v === 'string' && (v === 'stroll' || (SPOTS as readonly string[]).includes(v) || (NUDGES as readonly string[]).includes(v))
}

/** 闲逛一次走多远（像素）：随机落在这个范围里 */
export const STROLL_MIN = 80
export const STROLL_MAX = 220

/**
 * 闲逛：在原来的高度上往左或往右走一小段（你把她放在哪就在那附近，不跑远）。
 * 靠近一边时往另一边走；rand 返回 0 到 1（测试里传固定值）
 */
export function strollTarget(current: Rect, area: Rect, rand: () => number = Math.random): Point {
  const step = Math.round(STROLL_MIN + (STROLL_MAX - STROLL_MIN) * rand())
  const roomLeft = current.x - area.x
  const roomRight = area.x + area.width - (current.x + current.width)
  let dir = rand() < 0.5 ? -1 : 1
  if (roomLeft < STROLL_MAX) dir = 1
  else if (roomRight < STROLL_MAX) dir = -1
  const x = Math.max(area.x, Math.min(current.x + dir * step, area.x + area.width - current.width))
  return { x, y: current.y }
}

/** 什么时候可以闲逛：开了开关、她在桌面上能动、没在聊、没在睡，而且碰上这一次的运气（平均几分钟一次） */
export function shouldStroll(o: { enabled: boolean; visible: boolean; quiet: boolean; chatting: boolean; asleep: boolean }, rand: () => number = Math.random): boolean {
  return o.enabled && o.visible && !o.quiet && !o.chatting && !o.asleep && rand() < 0.3
}

/** 右键菜单里列的位置 */
export const SPOT_NAMES: Record<Spot, string> = {
  topleft: '左上角',
  top: '正上方',
  topright: '右上角',
  left: '左边',
  center: '中间',
  right: '右边',
  bottomleft: '左下角',
  bottom: '正下方',
  bottomright: '右下角'
}

// ---------- 认指令 ----------

/** 说法 → 位置。长的放前面（「左上」先于「左」） */
const PLACES: [RegExp, Spot][] = [
  [/^(?:左上|上左)(?:角|角落|边|方|面)?$/, 'topleft'],
  [/^(?:右上|上右)(?:角|角落|边|方|面)?$/, 'topright'],
  [/^(?:左下|下左)(?:角|角落|边|方|面)?$/, 'bottomleft'],
  [/^(?:右下|下右)(?:角|角落|边|方|面)?$/, 'bottomright'],
  [/^(?:正|最)?(?:上|顶)(?:边|面|方|头)?$/, 'top'],
  [/^(?:正|最)?(?:下|底)(?:边|面|方|下)?$/, 'bottom'],
  [/^(?:最)?左(?:边|面|侧)$/, 'left'],
  [/^(?:最)?右(?:边|面|侧)$/, 'right'],
  [/^(?:正)?(?:中间|中央|当中|正中)$/, 'center']
]

const VERB = '(?:去|到|走到|跑到|回到|挪到|移到|站到|待到|呆到|躲到|飘到|飞到|走去|跑去|挪去|回)'
const TAIL = '(?:去|来|吧|呀|啊|呗|一下|待着|呆着|待会儿|呆会儿)*'
const POLITE = '(?:你|艾拉|麻烦你?|帮我|给我|可以|能不能|能)*'

/** 去掉空白、句尾标点和语气词；全角半角统一不了的就算了 */
function tidy(text: string): string {
  return text.replace(/\s+/g, '').replace(/[。！？!?.,，~～、…]+$/u, '').replace(/^(?:那|嗯|好|喂)[，,]?/u, '')
}

const PLACE_RE = new RegExp(`^${POLITE}${VERB}?(?:屏幕|桌面)?(?:的)?(.+?)${TAIL}$`, 'u')
const NUDGE_RE = new RegExp(`^${POLITE}(?:再)?(?:往|向|朝)(左|右|上|下)(?:边|面)?(?:一点|一点点|点儿?|些|一些)?(?:挪挪|挪一挪|挪一下|挪|走走|走|移一下|移|靠|去|一下)*(?:一点|点儿?|些)?${TAIL}$`, 'u')
const HOME_RE = new RegExp(`^${POLITE}(?:回(?:去|来|原位|原来的地方|老地方|你的位置)|回到原位|回到原来的地方)${TAIL}$`, 'u')

/** 整句话就是让她走到某处时返回去哪；不是就返回 null（照常当聊天） */
export function parseMove(text: string): MoveTarget | null {
  const t = tidy(text)
  if (!t || t.length > 20) return null
  if (HOME_RE.test(t)) return 'bottomright'
  const n = NUDGE_RE.exec(t)
  if (n) return ({ 左: 'nudge-left', 右: 'nudge-right', 上: 'nudge-up', 下: 'nudge-down' } as const)[n[1] as '左' | '右' | '上' | '下']
  const m = PLACE_RE.exec(t)
  if (!m) return null
  // 没有「去、到……」这类动词时，只认带「角」或者说得很明确的位置（「右下角」「中间」）：一个字的「上」「左」不算
  const place = m[1]
  const hasVerb = new RegExp(`^${POLITE}${VERB}`, 'u').test(t)
  for (const [re, spot] of PLACES) {
    if (!re.test(place)) continue
    if (!hasVerb && !/角|中间|中央/.test(place)) return null
    return spot
  }
  return null
}

// ---------- 算位置 ----------

export interface Rect {
  x: number
  y: number
  width: number
  height: number
}

export interface Point {
  x: number
  y: number
}

/** 左右离屏幕边多远（和「回到右下角」原来的位置一样）；上下贴着工作区 */
export const SIDE_MARGIN = 40
/** 「往左一点」走多远 */
export const NUDGE_STEP = 240

/** 窗口左上角放在哪，整个窗口都在工作区里 */
export function targetPosition(target: MoveTarget, current: Rect, area: Rect): Point {
  const w = current.width
  const h = current.height
  const left = area.x + SIDE_MARGIN
  const right = area.x + area.width - w - SIDE_MARGIN
  const top = area.y
  const bottom = area.y + area.height - h
  const midX = Math.round(area.x + (area.width - w) / 2)
  const midY = Math.round(area.y + (area.height - h) / 2)
  let p: Point
  switch (target) {
    case 'topleft': p = { x: left, y: top }; break
    case 'top': p = { x: midX, y: top }; break
    case 'topright': p = { x: right, y: top }; break
    case 'left': p = { x: left, y: midY }; break
    case 'center': p = { x: midX, y: midY }; break
    case 'right': p = { x: right, y: midY }; break
    case 'bottomleft': p = { x: left, y: bottom }; break
    case 'bottom': p = { x: midX, y: bottom }; break
    case 'bottomright': p = { x: right, y: bottom }; break
    case 'nudge-left': p = { x: current.x - NUDGE_STEP, y: current.y }; break
    case 'nudge-right': p = { x: current.x + NUDGE_STEP, y: current.y }; break
    case 'nudge-up': p = { x: current.x, y: current.y - NUDGE_STEP }; break
    case 'nudge-down': p = { x: current.x, y: current.y + NUDGE_STEP }; break
    case 'stroll': p = strollTarget(current, area); break
  }
  // 屏幕比她还小这种极端情况：至少左上角在工作区里
  return {
    x: Math.max(area.x, Math.min(p.x, area.x + area.width - w)),
    y: Math.max(area.y, Math.min(p.y, area.y + area.height - h))
  }
}

// ---------- 走过去 ----------

/** 走多久（毫秒）：按距离，短的也不至于一闪而过，长的不拖太久 */
export function walkDuration(from: Point, to: Point): number {
  const dist = Math.hypot(to.x - from.x, to.y - from.y)
  return Math.round(Math.min(1600, Math.max(350, dist * 0.9)))
}

/** 走到一半时在哪：两头慢中间快，再带一点上下的小颠（像在走路），到了正好落在终点 */
export function walkPoint(from: Point, to: Point, t: number): Point {
  const k = Math.min(1, Math.max(0, t))
  const e = k < 0.5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2
  const bob = k > 0 && k < 1 ? -Math.abs(Math.sin(k * Math.PI * 6)) * 6 : 0
  return { x: Math.round(from.x + (to.x - from.x) * e), y: Math.round(from.y + (to.y - from.y) * e + bob) }
}
