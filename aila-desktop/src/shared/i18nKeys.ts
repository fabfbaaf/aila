/**
 * 已经换成英文界面的地方，有哪些中文原文要有英文（i18n.test.ts 和补词典时用）。
 *
 * 三种：
 * - t('…') / tm('…') / tr('…') / translate(lang, '…') 的第一个中文参数：用了这些的文件都扫（看 import 认）
 * - 用 localized(…) 包起来的表、在显示的地方才翻的常量表：表里所有的中文字符串
 * - 整个文件都是界面文字的（台词、页面名……）：文件里所有带中文的字符串
 */

/** 整个文件里带中文的字符串都是界面文字（相对 aila-desktop/） */
export const TABLE_FILES = [
  'src/renderer/src/persona/lines.ts',
  'src/shared/shell.ts',
  'src/renderer/src/shell/palette.ts',
  'src/renderer/src/agent/board.ts',
  'src/renderer/src/agent/commands.ts',
  'src/renderer/src/workbench/replay.ts',
  'src/renderer/src/gestures/GestureEngine.ts',
  'src/renderer/src/gestures/HandTracker.ts'
]

/** 别的文件里在显示时才翻的表：文件 → 表的名字（只取这些常量里的字符串） */
export const TABLES: Record<string, string[]> = {
  'src/renderer/src/settings/sections/MemorySection.vue': ['GROUPS'],
  'src/renderer/src/settings/sections/DesktopSection.vue': ['REASONS'],
  'src/renderer/src/settings/sections/AccountSection.vue': ['STATUS'],
  'src/renderer/src/settings/sections/StatusSection.vue': ['NAMES'],
  'src/renderer/src/settings/sections/ExtensionsSection.vue': ['LINE_NAMES'],
  'src/renderer/src/settings/sections/VoiceSection.vue': ['SAMPLE'],
  'src/renderer/src/agent/AgentApp.vue': ['WAITING'],
  'src/renderer/src/agent/PlanCard.vue': ['MARK'],
  'src/renderer/src/agent/fold.ts': ['MODE_NOTE'],
  'src/renderer/src/workbench/WorkbenchApp.vue': ['ECOSYSTEM', 'COLOR_MODES', 'kinds'],
  'src/shared/move.ts': ['SPOT_NAMES'],
  'src/shared/studio.ts': ['WORK_STATUS_TEXT'],
  'src/shared/agent.ts': ['MODE_LABELS', 'MODE_HINTS'],
  'src/shared/mcp.ts': ['MCP_STATUS_LABELS'],
  'src/shared/settings.ts': ['PERSONA_STYLES']
}

const CJK = /[一-鿿]/

/** 这个文件用上了翻译（import 了 t / tm / translate / localized） */
export function usesTranslation(src: string): boolean {
  return /import \{[^}]*\b(?:t|tm|translate|localized)\b[^}]*\} from '[./]+(?:i18n|lang)'/.test(src)
}

function unescape(s: string): string {
  return s.replace(/\\(.)/g, (_, c: string) => (c === 'n' ? '\n' : c))
}

/** 去掉注释（行注释、块注释、HTML 注释），免得把说明当成要翻的字 */
function stripComments(src: string): string {
  return src
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/(^|[^:'"`\\])\/\/[^\n]*/g, '$1')
}

/** t('…') / tm('…') / tr('…') / translate(lang, '…') 的第一个中文参数 */
export function calledKeys(src: string): string[] {
  const out: string[] = []
  const clean = stripComments(src)
  for (const m of clean.matchAll(/\b(?:t|tm|tr|tr2)\(\s*'((?:[^'\\]|\\.)*)'/g)) {
    const key = unescape(m[1])
    if (CJK.test(key)) out.push(key)
  }
  for (const m of clean.matchAll(/\btranslate\(\s*[\w.]+\s*,\s*'((?:[^'\\]|\\.)*)'/g)) {
    const key = unescape(m[1])
    if (CJK.test(key)) out.push(key)
  }
  return out
}

/** 文件里所有带中文的单引号字符串（常量表用） */
export function literalKeys(src: string): string[] {
  const out: string[] = []
  for (const m of stripComments(src).matchAll(/'((?:[^'\\\n]|\\.)*)'/g)) {
    const key = unescape(m[1])
    if (CJK.test(key)) out.push(key)
  }
  return out
}

/** 某个常量（const NAME … 到下一个空行）里的中文字符串 */
export function tableKeys(raw: string, name: string): string[] {
  const src = raw.replace(/\r\n/g, '\n')
  const at = src.search(new RegExp(`const ${name}\\b`))
  if (at < 0) return []
  const end = src.indexOf('\n\n', at)
  return literalKeys(src.slice(at, end < 0 ? undefined : end))
}
