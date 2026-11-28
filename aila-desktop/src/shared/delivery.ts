/**
 * 验收和交付：主进程、预加载、渲染层共用的类型（接口见 aila-ai 的 /agent/sessions/{id}/delivery 和 /acceptance）。
 *
 * 交付卡上的东西全是代码算的（改了哪些文件和 diff、最后一次测试、PR 和 CI），她自己的描述不放上来；
 * 验收记录（通过 / 退回修改）写在任务日志里，以后子任务交回、从验收过的阶段恢复都认这一份。
 */

export type Verdict = 'accepted' | 'rejected'

export interface DeliveryFile {
  path: string
  /** 加了几行、删了几行（二进制文件、看不了 diff 时是 null） */
  added: number | null
  deleted: number | null
  /** unified diff；太长的截断了，最后一行写着还有多少行 */
  diff: string
  /** 新建、还没 git add 的文件 */
  untracked: boolean
}

export type CheckState = 'pass' | 'fail' | 'pending' | 'skip'

export interface DeliveryCard {
  changes: { files: DeliveryFile[]; base: string | null; note: string }
  /** 最后一次跑测试（日志里最后一条像测试的命令）；没跑过是 null */
  tests: { command: string; ok: boolean; tail: string; seq?: number } | null
  branch: string | null
  pr: { url: string; owner: string; repo: string; number: number } | null
  /** 没有 PR、或者没问 CI 时是 null；none：PR 上没有检查；unknown：问不到 */
  ci: {
    state: 'pass' | 'fail' | 'pending' | 'none' | 'unknown'
    checks: { name: string; state: CheckState; link: string }[]
    source: 'gh' | 'api'
    note?: string
    merged?: boolean
    open?: boolean
  } | null
  /** 最新一次验收（没验收过是 null） */
  acceptance: AcceptanceEvent | null
  /** 现在算不算验收过：验收后又做了一轮就是 null */
  verdict: Verdict | null
}

/** 在 diff 上点某一行写的意见；side：new 是改后的行（加的、没动的），old 是删掉的行 */
export interface LineComment {
  file: string
  line: number
  side: 'old' | 'new'
  text: string
}

export interface AcceptanceEvent {
  type: 'acceptance'
  seq?: number
  t?: number
  verdict: Verdict
  note: string
  comments: LineComment[]
}

export const MAX_COMMENTS = 50
export const MAX_NOTE = 4000

/** 交付卡上能点开的链接：GitHub 上某个仓库里的页面（PR、检查的运行记录） */
export function isGithubLink(url: unknown): url is string {
  if (typeof url !== 'string' || url.length > 500) return false
  try {
    const u = new URL(url)
    return u.protocol === 'https:' && u.hostname === 'github.com' && !u.username && !u.password && !u.port &&
      /^\/[\w.-]+\/[\w.-]+\/\S*$/.test(u.pathname)
  } catch {
    return false
  }
}

/** 主进程转发前检查：不认识的字段、不对的行号都不往服务那边送 */
export function validAcceptance(verdict: unknown, note: unknown, comments: unknown): comments is LineComment[] {
  if (verdict !== 'accepted' && verdict !== 'rejected') return false
  if (typeof note !== 'string' || note.length > MAX_NOTE) return false
  if (!Array.isArray(comments) || comments.length > MAX_COMMENTS) return false
  return comments.every(
    (c) =>
      !!c && typeof c === 'object' &&
      typeof c.file === 'string' && c.file.length > 0 && c.file.length <= 500 &&
      Number.isSafeInteger(c.line) && c.line >= 1 &&
      (c.side === 'old' || c.side === 'new') &&
      typeof c.text === 'string' && c.text.trim().length > 0 && c.text.length <= 2000
  )
}
