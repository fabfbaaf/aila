/**
 * 主窗口「艾拉」里有哪些页面、地址怎么写。
 *
 * 以前是四个窗口（桌面上的她、工作窗口、代码星图、设置），现在除了桌面上的她都合进一个主窗口：
 * 左边一列图标切页面。主进程（打开到哪一页）和渲染层（显示哪一页）都用这里的规则，
 * 旧地址（#/agent、#/workbench、#/settings/分区）照样认，右键菜单、星图、屏幕报错这些入口不用改说法。
 */

import type { SettingsSection } from './settings'

export type Page = 'tasks' | 'projects' | 'knowledge' | 'memory' | 'settings'
/** 项目页里的两块：项目列表（索引状态）、地图（原来的代码星图） */
export type ProjectsTab = 'list' | 'map'

export interface ShellRoute {
  page: Page
  /** 设置页：哪个分区 */
  section?: SettingsSection
  /** 项目页：列表还是地图 */
  tab?: ProjectsTab
}

export const PAGES: { id: Page; name: string; icon: string; key: string }[] = [
  { id: 'tasks', name: '任务', icon: '▤', key: '1' },
  { id: 'projects', name: '项目', icon: '◇', key: '2' },
  { id: 'knowledge', name: '资料', icon: '▦', key: '3' },
  { id: 'memory', name: '记忆', icon: '✎', key: '4' },
  { id: 'settings', name: '设置', icon: '⚙', key: '5' }
]

/** 设置页里剩下的分区（资料库、记忆、代码库各自成了一页） */
export const SETTINGS_TABS: { id: SettingsSection; name: string }[] = [
  { id: 'model', name: '对话' },
  { id: 'voice', name: '语音' },
  { id: 'desktop', name: '桌面' },
  { id: 'skills', name: '技能包' },
  { id: 'account', name: '账号与同步' },
  { id: 'appearance', name: '形象' },
  { id: 'status', name: '运行状态' }
]

const PAGE_IDS = new Set<string>(PAGES.map((p) => p.id))
const TAB_IDS = new Set<string>(SETTINGS_TABS.map((t) => t.id))

/** 旧的设置分区 → 现在在哪一页 */
export function routeForSection(section: SettingsSection | undefined): ShellRoute {
  if (section === 'knowledge') return { page: 'knowledge' }
  if (section === 'memory') return { page: 'memory' }
  if (section === 'code') return { page: 'projects', tab: 'list' }
  return { page: 'settings', section: section && TAB_IDS.has(section) ? section : 'model' }
}

/** 地址（location.hash，带不带 # 都行）→ 页面。认不出来的都去任务页 */
export function parseHash(hash: string): ShellRoute {
  const parts = hash.replace(/^#?\/?/, '').split('/').filter(Boolean)
  const [head, sub] = parts
  if (head === 'agent' || head === 'tasks') return { page: 'tasks' }
  if (head === 'workbench') return { page: 'projects', tab: 'map' }
  if (head === 'settings') return routeForSection(sub as SettingsSection | undefined)
  if (head === 'projects') return { page: 'projects', tab: sub === 'map' ? 'map' : 'list' }
  if (head && PAGE_IDS.has(head)) return { page: head as Page }
  return { page: 'tasks' }
}

/** 页面 → 地址（不带 #） */
export function toHash(r: ShellRoute): string {
  if (r.page === 'settings') return `/settings/${r.section ?? 'model'}`
  if (r.page === 'projects') return `/projects/${r.tab ?? 'list'}`
  return `/${r.page}`
}

/** 主进程传过来的页面先检查一遍（只认上面这些） */
export function cleanRoute(raw: unknown): ShellRoute {
  if (!raw || typeof raw !== 'object') return { page: 'tasks' }
  const r = raw as Record<string, unknown>
  return parseHash(toHash({
    page: typeof r.page === 'string' && PAGE_IDS.has(r.page) ? (r.page as Page) : 'tasks',
    section: typeof r.section === 'string' ? (r.section as SettingsSection) : undefined,
    tab: r.tab === 'map' ? 'map' : 'list'
  }))
}
