/**
 * 扩展包：AI 服务 /extensions 接口的数据（主进程、预加载、设置页共用）。格式和安全边界见 aila-ai/app/extensions.py。
 * 选文件、弹窗都在主进程：渲染层不能让 AI 服务去读任意路径，只能拿主进程弹窗选出来的那一个。
 */
import type { McpStatus, McpToolInfo } from './mcp'
import { translate, type Lang } from './i18n'

export interface ExtensionSetting {
  key: string
  label: string
  secret: boolean
  required: boolean
  description: string
  /** 填没填（密钥类的值不回传） */
  set: boolean
  /** 不是密钥的设置：现在的值 */
  value: string
}

export interface ExtensionServer {
  name: string
  /** MCP 那边的服务名「扩展id-服务名」（工具叫 mcp__它__工具名） */
  serverName: string
  disabled: boolean
  readOnly: boolean
  status: McpStatus
  error: string
  command: string
  tools: McpToolInfo[]
  log: string[]
}

export interface ExtensionInfo extends ExtensionExtras {
  id: string
  name: string
  version: string
  description: string
  enabled: boolean
  folder: string
  commands: string[]
  skills: { name: string; description: string }[]
  servers: ExtensionServer[]
  settings: ExtensionSetting[]
  /** 必填但还没填的设置（填了才能打开工具服务） */
  missing: string[]
  requires: string[]
}

/** 动作、台词、形象、页面（第二、三期）：清单和列表里都有 */
export interface ExtensionExtras {
  motions: { name: string; label: string }[]
  /** 台词池 → 加了几句 */
  lines: Record<string, number>
  /** 整组换掉这些场合的台词，而不是往里加 */
  linesReplace: boolean
  /** kind：vrm 一个文件；live2d 一整个文件夹（要先装 Live2D 运行库） */
  avatars: { name: string; size: number; kind?: 'vrm' | 'live2d' }[]
  pages: { name: string; title: string }[]
}

export interface ExtensionList {
  extensions: ExtensionInfo[]
  /** 装在哪（给你看，方便自己打开看看） */
  folder: string
}

/** 装之前给你看的清单：AI 服务从说明书和文件算出来的，不是扩展自己的介绍 */
export interface ExtensionPlan extends ExtensionExtras {
  id: string
  name: string
  version: string
  description: string
  commands: string[]
  skills: { name: string; description: string }[]
  servers: { name: string; command: string; readOnly: boolean; env: string[] }[]
  settings: { key: string; label: string; secret: boolean; required: boolean; description: string }[]
  requires: string[]
  warnings: string[]
  /** 已经装了旧版：这次是升级 */
  upgrade: boolean
  /** 从哪个目录下载的（从目录装时才有） */
  source?: string
}

export interface ExtensionUpdate {
  enabled?: boolean
  settings?: Record<string, string>
  /** 服务名 → 停用？ */
  servers?: Record<string, boolean>
}

const ID = /^[a-z0-9][a-z0-9-]{0,23}$/
const TOKEN = /^[0-9a-f]{16}$/
const KEY = /^[A-Za-z][A-Za-z0-9_]{0,31}$/
const SERVER = /^[A-Za-z0-9_]{1,7}$/

export function validExtensionId(id: unknown): id is string {
  return typeof id === 'string' && ID.test(id)
}

export function validStageToken(token: unknown): token is string {
  return typeof token === 'string' && TOKEN.test(token)
}

/** 渲染层发来的修改：只收认识的字段、合规的键，值有长度上限；不对就返回 null */
export function cleanUpdate(raw: unknown): ExtensionUpdate | null {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null
  const r = raw as Record<string, unknown>
  const out: ExtensionUpdate = {}
  if (r.enabled !== undefined) {
    if (typeof r.enabled !== 'boolean') return null
    out.enabled = r.enabled
  }
  if (r.settings !== undefined) {
    if (!r.settings || typeof r.settings !== 'object' || Array.isArray(r.settings)) return null
    const entries = Object.entries(r.settings as Record<string, unknown>)
    if (entries.length > 50 || !entries.every(([k, v]) => KEY.test(k) && typeof v === 'string' && v.length <= 2000)) return null
    out.settings = Object.fromEntries(entries) as Record<string, string>
  }
  if (r.servers !== undefined) {
    if (!r.servers || typeof r.servers !== 'object' || Array.isArray(r.servers)) return null
    const entries = Object.entries(r.servers as Record<string, unknown>)
    if (entries.length > 20 || !entries.every(([k, v]) => SERVER.test(k) && typeof v === 'boolean')) return null
    out.servers = Object.fromEntries(entries) as Record<string, boolean>
  }
  return Object.keys(out).length ? out : null
}

/** 列表里每个扩展的一行小结：「2 个命令 · 1 项本事 · 1 个工具服务（开着 1 个）」 */
/** lang：界面语言（页面传进来；不传是中文） */
export function summarize(e: Pick<ExtensionInfo, 'commands' | 'skills' | 'servers'> & Partial<ExtensionExtras>, lang: Lang = 'zh'): string {
  const tr = (zh: string, n: number): string => translate(lang, zh, { n })
  const parts: string[] = []
  if (e.commands.length) parts.push(tr('{n} 个命令', e.commands.length))
  if (e.skills.length) parts.push(tr('{n} 项本事', e.skills.length))
  if (e.servers.length) {
    const on = e.servers.filter((s) => !s.disabled).length
    parts.push(tr('{n} 个工具服务', e.servers.length) + (on ? tr('（开着 {n} 个）', on) : translate(lang, '（都停着）')))
  }
  if (e.motions?.length) parts.push(tr('{n} 个动作', e.motions.length))
  const lines = Object.values(e.lines ?? {}).reduce((a, b) => a + b, 0)
  if (lines) parts.push(tr('{n} 句台词', lines))
  if (e.avatars?.length) parts.push(tr('{n} 个形象', e.avatars.length))
  if (e.pages?.length) parts.push(tr('{n} 个页面', e.pages.length))
  return parts.join(' · ') || translate(lang, '里面什么都没有')
}

// ---------- 第二期：桌面伙伴（动作、台词、形象） ----------

/** 扩展动作的一段：和内置动作（Behavior 的 Phase）同一个格式 */
export interface MotionPhase {
  dur: number
  pose?: Partial<Record<PoseKey, number>>
  expr?: Partial<Record<ExprKey, number>>
}

export type PoseKey = 'headPitch' | 'headYaw' | 'headRoll' | 'bodyLean' | 'bodySway' | 'armRaise' | 'forearmSwing' | 'armsUp'
export type ExprKey = 'happy' | 'sad' | 'surprised' | 'relaxed' | 'blink'

/** 和 AI 服务（app/extension_extras.py）一样的范围；这边再截一次，不只信那边 */
export const POSE_LIMITS: Record<PoseKey, [number, number]> = {
  headPitch: [-0.6, 0.6], headYaw: [-0.8, 0.8], headRoll: [-0.5, 0.5], bodyLean: [-0.3, 0.3], bodySway: [-0.3, 0.3],
  armRaise: [0, 1], forearmSwing: [-1, 1], armsUp: [0, 1]
}
const EXPR_KEYS: ExprKey[] = ['happy', 'sad', 'surprised', 'relaxed', 'blink']
const MOTION_NAME = /^[a-z][a-z0-9_]{1,19}$/

export interface Companion {
  motions: Record<string, { label: string; phases: MotionPhase[] }>
  lines: { add: Record<string, string[]>; replace: Record<string, string[]> }
  avatars: { extension: string; extensionName: string; name: string; file: string; size: number; kind?: 'vrm' | 'live2d' }[]
}

const clamp = (v: number, [lo, hi]: [number, number]): number => Math.min(hi, Math.max(lo, v))

/** 扩展的动作 → 能直接播放的几段姿态：不认识的键丢掉、值截到范围里、太长的不要；不对就返回 null */
export function cleanMotion(phases: unknown): MotionPhase[] | null {
  if (!Array.isArray(phases) || phases.length === 0 || phases.length > 40) return null
  const out: MotionPhase[] = []
  let total = 0
  for (const raw of phases) {
    if (!raw || typeof raw !== 'object') return null
    const p = raw as Record<string, unknown>
    if (typeof p.dur !== 'number' || !(p.dur >= 0.05 && p.dur <= 5)) return null
    total += p.dur
    const phase: MotionPhase = { dur: p.dur }
    if (p.pose && typeof p.pose === 'object') {
      const pose: Partial<Record<PoseKey, number>> = {}
      for (const [k, v] of Object.entries(p.pose)) {
        if (k in POSE_LIMITS && typeof v === 'number' && Number.isFinite(v)) pose[k as PoseKey] = clamp(v, POSE_LIMITS[k as PoseKey])
      }
      phase.pose = pose
    }
    if (p.expr && typeof p.expr === 'object') {
      const expr: Partial<Record<ExprKey, number>> = {}
      for (const [k, v] of Object.entries(p.expr)) {
        if ((EXPR_KEYS as string[]).includes(k) && typeof v === 'number' && Number.isFinite(v)) expr[k as ExprKey] = clamp(v, [0, 1])
      }
      phase.expr = expr
    }
    out.push(phase)
  }
  return total <= 10 ? out : null
}

/** 扩展的动作表 → 名字合规、能播放的那些 */
export function cleanMotions(raw: Companion['motions'] | undefined, builtin: readonly string[]): Record<string, MotionPhase[]> {
  const out: Record<string, MotionPhase[]> = {}
  for (const [name, m] of Object.entries(raw ?? {})) {
    if (!MOTION_NAME.test(name) || builtin.includes(name)) continue
    const phases = cleanMotion(m?.phases)
    if (phases) out[name] = phases
  }
  return out
}

/**
 * 台词池：内置的 + 扩展加的；扩展说整组换掉的就只用扩展的。
 * 只动内置里本来就是句子列表的那些键，每句限长。
 */
export function mergeLines(base: Record<string, readonly string[]>, extra: Companion['lines'] | undefined): Record<string, string[]> {
  const out: Record<string, string[]> = {}
  const ok = (list: unknown): string[] =>
    Array.isArray(list) ? list.filter((t): t is string => typeof t === 'string' && t.trim().length > 0).map((t) => t.slice(0, 80)).slice(0, 30) : []
  for (const [key, lines] of Object.entries(base)) {
    const replaced = ok(extra?.replace?.[key])
    out[key] = replaced.length ? replaced : [...lines, ...ok(extra?.add?.[key])]
  }
  return out
}

// ---------- 第三期：目录 ----------

export interface CatalogEntry {
  id: string
  name: string
  version: string
  description: string
  size: number | null
  /** 装了的版本；没装是 null */
  installed: string | null
  upgrade: boolean
}

export interface CatalogListing {
  sources: string[]
  catalogs: { source: string; name: string; error: string; extensions: CatalogEntry[] }[]
}

/** 目录地址：https 网址或者本机的完整路径；每行一个，最多 10 个 */
export function cleanSources(raw: unknown): string[] | null {
  if (!Array.isArray(raw) || raw.length > 10) return null
  const out: string[] = []
  for (const s of raw) {
    if (typeof s !== 'string') return null
    const t = s.trim()
    if (!t) continue
    if (t.length > 1000) return null
    out.push(t)
  }
  return out
}

// ---------- 主进程用的检查（第二、三期） ----------

const PAGE_NAME = /^[\w一-鿿-]{1,40}$/

export function validPageName(name: unknown): name is string {
  return typeof name === 'string' && PAGE_NAME.test(name)
}

export function validMotionName(name: unknown): name is string {
  return typeof name === 'string' && MOTION_NAME.test(name)
}

/** 扩展形象的文件：必须是 …/extensions/<扩展 id>/avatars/<名字>.vrm（防止 AI 服务那边给出别的地方） */
export function avatarPathOk(file: string, extId: string, name: string): boolean {
  const norm = file.replace(/\\/g, '/')
  if (norm.includes('/../')) return false
  if (norm.endsWith(`/extensions/${extId}/avatars/${name}.vrm`)) return true
  // Live2D：…/avatars/<名字>/<随便>.model3.json（就在那个文件夹里，不能再往下一层）
  const base = `/extensions/${extId}/avatars/${name}/`
  const at = norm.lastIndexOf(base)
  return at >= 0 && /^[^/]+\.model3\.json$/i.test(norm.slice(at + base.length))
}

/** 从扩展列表里找形象文件 */
export function findAvatar(companion: Companion | undefined, extId: string, name: string): string | null {
  const found = companion?.avatars.find((a) => a.extension === extId && a.name === name)
  return found && avatarPathOk(found.file, extId, name) ? found.file : null
}

