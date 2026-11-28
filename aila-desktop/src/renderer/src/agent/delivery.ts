/**
 * 交付卡的纯逻辑（可以单测）：把 unified diff 拆成一行行（带新旧行号），点哪一行能写意见，卡片顶上的一行概况。
 *
 * 行号跟 git 的一样：加的行、没动的行用改后的行号（side: new），删掉的行用改前的行号（side: old）；
 * 这样退回修改时「src/cart.py 第 12 行」她打开文件就能对上。
 */
import type { DeliveryCard, LineComment } from '../../../shared/delivery'

export type DiffKind = 'meta' | 'hunk' | 'add' | 'del' | 'ctx' | 'more'

export interface DiffLine {
  kind: DiffKind
  text: string
  old: number | null
  new: number | null
}

const HUNK = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/

export function parseDiff(diff: string): DiffLine[] {
  const out: DiffLine[] = []
  let oldNo = 0
  let newNo = 0
  let inHunk = false
  for (const raw of diff.replace(/\r\n/g, '\n').split('\n')) {
    const hunk = HUNK.exec(raw)
    if (hunk) {
      oldNo = Number(hunk[1])
      newNo = Number(hunk[2])
      inHunk = true
      out.push({ kind: 'hunk', text: raw, old: null, new: null })
    } else if (!inHunk) {
      if (raw) out.push({ kind: 'meta', text: raw, old: null, new: null })
    } else if (raw.startsWith('… ')) {
      out.push({ kind: 'more', text: raw, old: null, new: null }) // 「… 还有 120 行没显示」
    } else if (raw.startsWith('+')) {
      out.push({ kind: 'add', text: raw.slice(1), old: null, new: newNo++ })
    } else if (raw.startsWith('-')) {
      out.push({ kind: 'del', text: raw.slice(1), old: oldNo++, new: null })
    } else if (raw.startsWith('\\')) {
      out.push({ kind: 'meta', text: raw, old: null, new: null }) // \ No newline at end of file
    } else if (raw.startsWith(' ')) {
      out.push({ kind: 'ctx', text: raw.slice(1), old: oldNo++, new: newNo++ })
    } else if (raw !== '') {
      // 下一个文件的 diff --git 之类（git 在一段里不会出现真正的空行：没动的空行是一个空格）
      inHunk = false
      out.push({ kind: 'meta', text: raw, old: null, new: null })
    }
  }
  return out
}

/** 点这一行写意见时记哪个行号（标题、说明行不能点） */
export function commentTarget(line: DiffLine): { line: number; side: 'old' | 'new' } | null {
  if (line.kind === 'add' || line.kind === 'ctx') return line.new === null ? null : { line: line.new, side: 'new' }
  if (line.kind === 'del') return line.old === null ? null : { line: line.old, side: 'old' }
  return null
}

export function sameSpot(c: Pick<LineComment, 'file' | 'line' | 'side'>, file: string, at: { line: number; side: 'old' | 'new' }): boolean {
  return c.file === file && c.line === at.line && c.side === at.side
}

/** 写一条意见：同一行再写一次就是改掉原来的，写空了就是删掉 */
export function putComment(list: LineComment[], next: LineComment): LineComment[] {
  const rest = list.filter((c) => !sameSpot(c, next.file, next))
  return next.text.trim() ? [...rest, { ...next, text: next.text.trim() }] : rest
}

export interface DeliveryBrief {
  files: number
  added: number
  deleted: number
  tests: 'pass' | 'fail' | null
  ci: NonNullable<DeliveryCard['ci']>['state'] | null
}

export function brief(card: DeliveryCard): DeliveryBrief {
  const files = card.changes.files
  return {
    files: files.length,
    added: files.reduce((n, f) => n + (f.added ?? 0), 0),
    deleted: files.reduce((n, f) => n + (f.deleted ?? 0), 0),
    tests: card.tests ? (card.tests.ok ? 'pass' : 'fail') : null,
    ci: card.ci?.state ?? null
  }
}

/** 有没有东西可以交付：改过文件、跑过测试或者开了 PR（只聊天、只读的任务不显示交付卡） */
export function hasDelivery(card: DeliveryCard | null): card is DeliveryCard {
  return !!card && (card.changes.files.length > 0 || !!card.tests || !!card.pr || !!card.acceptance)
}
