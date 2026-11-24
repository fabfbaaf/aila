/**
 * 已经换成英文界面的文件里，有哪些中文原文要有英文（i18n.test.ts 和补词典时用）。
 *
 * 两种：t('…') / tm('…') 的第一个参数；在显示的地方才翻的常量表（台词、页面名、分区名……），表里所有的中文字符串。
 * 还没换的文件（任务页、地图、作品页）不在这里，那些地方照旧显示中文。
 */

/** 已经换成 t() / tm() 的文件（相对 aila-desktop/） */
export const TRANSLATED_FILES = [
  'src/main/index.ts',
  'src/renderer/src/App.vue',
  'src/renderer/src/chat/Conversation.ts',
  'src/renderer/src/behavior/Behavior.ts',
  'src/renderer/src/voice/VoiceController.ts',
  'src/renderer/src/persona/lines.ts',
  'src/renderer/src/components/ChatInput.vue',
  'src/renderer/src/components/SpeechBubble.vue',
  'src/renderer/src/shell/ShellApp.vue',
  'src/renderer/src/shell/ChatHistoryPage.vue',
  'src/renderer/src/shell/CommandPalette.vue',
  'src/renderer/src/shell/SettingsPage.vue',
  'src/renderer/src/shell/SectionPage.vue',
  'src/renderer/src/shell/palette.ts',
  'src/renderer/src/settings/sections/AccountSection.vue',
  'src/renderer/src/settings/sections/AgentSection.vue',
  'src/renderer/src/settings/sections/AppearanceSection.vue',
  'src/renderer/src/settings/sections/CodeSection.vue',
  'src/renderer/src/settings/sections/DesktopSection.vue',
  'src/renderer/src/settings/sections/ExtensionsSection.vue',
  'src/renderer/src/settings/sections/KnowledgeSection.vue',
  'src/renderer/src/settings/sections/McpCard.vue',
  'src/renderer/src/settings/sections/MemorySection.vue',
  'src/renderer/src/settings/sections/ModelSection.vue',
  'src/renderer/src/settings/sections/SkillsSection.vue',
  'src/renderer/src/settings/sections/StatusSection.vue',
  'src/renderer/src/settings/sections/VoiceSection.vue'
]

/** 在显示的地方才翻的常量表所在的文件：这些文件里所有带中文的字符串都要有英文 */
export const TABLE_FILES = [
  'src/renderer/src/persona/lines.ts',
  'src/shared/shell.ts',
  'src/renderer/src/shell/palette.ts'
]

/** 别的文件里在显示时才翻的几个表：文件 → 表的名字（只取这些常量里的字符串） */
export const TABLES: Record<string, string[]> = {
  'src/renderer/src/settings/sections/MemorySection.vue': ['GROUPS'],
  'src/renderer/src/settings/sections/DesktopSection.vue': ['REASONS'],
  'src/renderer/src/settings/sections/AccountSection.vue': ['STATUS'],
  'src/renderer/src/settings/sections/StatusSection.vue': ['NAMES'],
  'src/renderer/src/settings/sections/ExtensionsSection.vue': ['LINE_NAMES'],
  'src/renderer/src/settings/sections/VoiceSection.vue': ['SAMPLE'],
  'src/shared/move.ts': ['SPOT_NAMES']
}

const CJK = /[一-鿿]/

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

/** t('…') / tm('…') 的第一个参数 */
export function calledKeys(src: string): string[] {
  const out: string[] = []
  for (const m of stripComments(src).matchAll(/\b(?:t|tm)\(\s*'((?:[^'\\]|\\.)*)'/g)) {
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
  const at = src.indexOf(`const ${name}`)
  if (at < 0) return []
  const end = src.indexOf('\n\n', at)
  return literalKeys(src.slice(at, end < 0 ? undefined : end))
}
