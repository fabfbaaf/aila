/**
 * 输入框里打「/」时弹出的命令菜单：内置的（/review、/plan）加上自定义命令（项目里的、自己的）。
 * 抽成纯函数方便单测；展开命令文件是 AI 服务做的，这里只管列出来、按前缀筛。
 */
import type { AgentCommand } from '../../../shared/agent'

export interface MenuItem {
  name: string
  description: string
  source: 'builtin' | AgentCommand['source']
}

const BUILTIN: MenuItem[] = [
  { name: 'plan', description: '先出分阶段的计划，批准后一段一段做', source: 'builtin' },
  { name: 'review', description: '只读地审一遍没提交的改动', source: 'builtin' }
]

export const SOURCE_LABEL: Record<MenuItem['source'], string> = { builtin: '内置', project: '项目', user: '我的' }

/**
 * 现在输入的是「/开头、还没打空格」时，列出名字对得上的命令（内置的在前）；否则空列表。
 * 电脑任务没有 /review
 */
export function commandMenu(text: string, custom: AgentCommand[], computer = false, limit = 8): MenuItem[] {
  if (!text.startsWith('/') || text.startsWith('//') || /\s/.test(text)) return []
  const q = text.slice(1).toLowerCase()
  const builtin = BUILTIN.filter((c) => !(computer && c.name === 'review'))
  const all: MenuItem[] = [...builtin, ...custom.map((c) => ({ name: c.name, description: c.description, source: c.source }))]
  return all.filter((c) => c.name.toLowerCase().startsWith(q)).slice(0, limit)
}

/**
 * 菜单开着时按了 Enter：输入已经是选中的那个完整命令名 → 不拦着（照常发送）；否则补全成「/名字 」
 */
export function pickOnEnter(text: string, item: MenuItem | undefined): string | null {
  if (!item) return null
  return text.toLowerCase() === `/${item.name.toLowerCase()}` ? null : `/${item.name} `
}
