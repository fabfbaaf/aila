/**
 * 快捷跳转（主窗口里按 Ctrl+K）：一个搜索框，能跳页面、设置分区，开新任务，搜聊过的话和她记得的事。
 *
 * 为什么这样设计：纯函数，只管「有哪些条目、按输入怎么排」，打开哪页、做什么由 ShellApp 执行。
 * 匹配就是子串（中文不分词也够用）：空格分开的几个词都要出现；标题开头对上的排最前，标题里有的其次，只在说明里有的最后。
 * 没输入时只列页面和常用动作；聊过的话、记忆要输入了才搜（不在空框里摆出你的私事）。
 */

import { PAGES, SETTINGS_TABS, type ShellRoute } from '../../../shared/shell'
import type { StoredMemory, StoredMessage } from '../../../shared/cloud'

export type PaletteAct =
  | { kind: 'go'; route: ShellRoute }
  | { kind: 'new-task' }
  | { kind: 'open-chat' }
  /** 去对话页，搜框里填上这段 */
  | { kind: 'chat'; query: string }

export interface PaletteItem {
  id: string
  group: '页面' | '设置' | '动作' | '对话' | '记忆'
  title: string
  hint: string
  act: PaletteAct
}

/** tr：把中文原文换成现在的语言（页面里传 t；测试里不传就是中文） */
export function staticItems(tr: (zh: string) => string = (s) => s): PaletteItem[] {
  const pages: PaletteItem[] = PAGES.map((p) => ({
    id: `page:${p.id}`,
    group: '页面',
    title: tr(p.name),
    hint: `Ctrl+${p.key}`,
    act: { kind: 'go', route: p.id === 'projects' ? { page: 'projects', tab: 'list' } : { page: p.id } }
  }))
  const extra: PaletteItem[] = [
    { id: 'page:map', group: '页面', title: tr('地图'), hint: '项目的代码星图', act: { kind: 'go', route: { page: 'projects', tab: 'map' } } }
  ]
  const settings: PaletteItem[] = SETTINGS_TABS.map((t) => ({
    id: `settings:${t.id}`,
    group: '设置',
    title: `${tr('设置')} · ${tr(t.name)}`,
    hint: '',
    act: { kind: 'go', route: { page: 'settings', section: t.id } }
  }))
  const actions: PaletteItem[] = [
    { id: 'act:new-task', group: '动作', title: tr('开新任务'), hint: '编程或电脑任务', act: { kind: 'new-task' } },
    { id: 'act:chat', group: '动作', title: tr('在这里和她聊'), hint: '对话页', act: { kind: 'go', route: { page: 'chat' } } },
    { id: 'act:open-chat', group: '动作', title: tr('在桌面上和她说话'), hint: 'Alt+Shift+A', act: { kind: 'open-chat' } },
    { id: 'act:import', group: '动作', title: tr('导入扩展、技能包'), hint: '技能和扩展', act: { kind: 'go', route: { page: 'settings', section: 'extensions' } } },
    { id: 'act:model', group: '动作', title: tr('填大模型密钥'), hint: '设置 · 对话', act: { kind: 'go', route: { page: 'settings', section: 'model' } } }
  ]
  return [...actions, ...pages, ...extra, ...settings]
}

/** 聊过的话里含这些词的（新的在前），最多 limit 条 */
export function historyItems(messages: StoredMessage[], query: string, limit = 8): PaletteItem[] {
  const words = words_(query)
  if (!words.length) return []
  const out: PaletteItem[] = []
  for (let i = messages.length - 1; i >= 0 && out.length < limit; i--) {
    const m = messages[i]
    if (m.deleted) continue
    const text = m.content.replace(/\[[a-z-]+\]/g, '')
    if (!words.every((w) => text.toLowerCase().includes(w))) continue
    out.push({
      id: `chat:${m.id}`,
      group: '对话',
      title: snippet(text, words[0]),
      hint: `${m.role === 'user' ? '你' : '她'} · ${m.createdAt.slice(0, 10)}`,
      act: { kind: 'chat', query: query.trim() }
    })
  }
  return out
}

/** 她记得的事里含这些词的 */
export function memoryItems(memories: StoredMemory[], query: string, limit = 6): PaletteItem[] {
  const words = words_(query)
  if (!words.length) return []
  return memories
    .filter((m) => !m.deleted && words.every((w) => m.content.toLowerCase().includes(w)))
    .slice(0, limit)
    .map((m) => ({ id: `memory:${m.id}`, group: '记忆' as const, title: m.content, hint: '她记得的事', act: { kind: 'go' as const, route: { page: 'memory' as const } } }))
}

/** 固定条目按输入排序；没输入时原样（动作、页面在前） */
export function searchStatic(items: PaletteItem[], query: string): PaletteItem[] {
  const words = words_(query)
  if (!words.length) return items.filter((i) => i.group !== '设置')
  const scored: [PaletteItem, number][] = []
  for (const item of items) {
    const title = item.title.toLowerCase()
    const all = `${title} ${item.hint.toLowerCase()}`
    if (!words.every((w) => all.includes(w))) continue
    const q = words.join(' ')
    const score = title.startsWith(q) || title.replace(/^[^·]+ · /, '').startsWith(q) ? 3 : words.every((w) => title.includes(w)) ? 2 : 1
    scored.push([item, score])
  }
  return scored.sort((a, b) => b[1] - a[1]).map(([i]) => i)
}

function words_(query: string): string[] {
  return query.toLowerCase().split(/\s+/).filter(Boolean)
}

/** 找到的词前后各留一点，太长的省略 */
export function snippet(text: string, word: string, room = 24): string {
  const flat = text.replace(/\s+/g, ' ').trim()
  const at = flat.toLowerCase().indexOf(word.toLowerCase())
  if (at < 0 || flat.length <= room * 2 + word.length) return flat.slice(0, room * 2 + word.length)
  const start = Math.max(0, at - room)
  const end = Math.min(flat.length, at + word.length + room)
  return `${start > 0 ? '…' : ''}${flat.slice(start, end)}${end < flat.length ? '…' : ''}`
}
