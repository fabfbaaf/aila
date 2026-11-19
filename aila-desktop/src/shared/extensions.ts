/**
 * 扩展包：AI 服务 /extensions 接口的数据（主进程、预加载、设置页共用）。格式和安全边界见 aila-ai/app/extensions.py。
 * 选文件、弹窗都在主进程：渲染层不能让 AI 服务去读任意路径，只能拿主进程弹窗选出来的那一个。
 */
import type { McpStatus, McpToolInfo } from './mcp'

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

export interface ExtensionInfo {
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

export interface ExtensionList {
  extensions: ExtensionInfo[]
  /** 装在哪（给你看，方便自己打开看看） */
  folder: string
}

/** 装之前给你看的清单：AI 服务从说明书和文件算出来的，不是扩展自己的介绍 */
export interface ExtensionPlan {
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
export function summarize(e: Pick<ExtensionInfo, 'commands' | 'skills' | 'servers'>): string {
  const parts: string[] = []
  if (e.commands.length) parts.push(`${e.commands.length} 个命令`)
  if (e.skills.length) parts.push(`${e.skills.length} 项本事`)
  if (e.servers.length) {
    const on = e.servers.filter((s) => !s.disabled).length
    parts.push(`${e.servers.length} 个工具服务${on ? `（开着 ${on} 个）` : '（都停着）'}`)
  }
  return parts.join(' · ') || '里面什么都没有'
}
