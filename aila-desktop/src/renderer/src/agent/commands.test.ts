import { describe, expect, it } from 'vitest'
import type { AgentCommand } from '../../../shared/agent'
import { commandMenu, pickOnEnter } from './commands'
import { Transcript } from './fold'

const custom: AgentCommand[] = [
  { name: '发布', description: '发版', source: 'project', path: 'p/.aila/commands/发布.md' },
  { name: '周报', description: '写周报', source: 'user', path: 'u/commands/周报.md' }
]

describe('命令菜单', () => {
  it('只在「/开头、还没打空格」时弹，按前缀筛，内置的在前', () => {
    expect(commandMenu('/', custom).map((c) => c.name)).toEqual(['plan', 'review', 'commit', 'pr', 'pr-comments', '发布', '周报'])
    expect(commandMenu('/p', custom).map((c) => c.name)).toEqual(['plan', 'pr', 'pr-comments'])
    expect(commandMenu('/发', custom).map((c) => [c.name, c.source])).toEqual([['发布', 'project']])
    expect(commandMenu('/PR-', custom).map((c) => c.name)).toEqual(['pr-comments'])
    for (const text of ['', '发布', '/发布 v1', '//x', '/x\ny']) expect(commandMenu(text, custom)).toEqual([])
  })

  it('电脑任务没有 /review、/commit、/pr', () => {
    expect(commandMenu('/', [], true).map((c) => c.name)).toEqual(['plan'])
  })

  it('Enter：没打全就补全，已经是完整的命令名就照常发送', () => {
    const [item] = commandMenu('/发', custom)
    expect(pickOnEnter('/发', item)).toBe('/发布 ')
    expect(pickOnEnter('/发布', item)).toBeNull()
    expect(pickOnEnter('/x', undefined)).toBeNull()
  })

  it('会话里的自定义命令：显示「/发布 v1.2」，展开能看到真正发的话', () => {
    const t = new Transcript()
    t.apply({ type: 'user', seq: 1, t: 1, text: '按这个顺序发版本 v1.2：……', command: '发布', title: '/发布 v1.2' })
    expect(t.items[0]).toMatchObject({ kind: 'user', tag: 'command', text: '/发布 v1.2', detail: '按这个顺序发版本 v1.2：……' })
  })
})
