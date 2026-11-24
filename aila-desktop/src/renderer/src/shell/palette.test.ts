import { describe, expect, it } from 'vitest'
import type { StoredMemory, StoredMessage } from '../../../shared/cloud'
import { historyItems, memoryItems, searchStatic, snippet, staticItems } from './palette'

const ITEMS = staticItems()

describe('Ctrl+K 快捷跳转', () => {
  it('没输入时列动作和页面，不列设置分区和私事', () => {
    const shown = searchStatic(ITEMS, '')
    expect(shown[0].title).toBe('开新任务')
    expect(shown.some((i) => i.group === '设置')).toBe(false)
    expect(historyItems([msg('a', '我在杭州')], '')).toEqual([])
  })

  it('标题开头对上的排最前；设置分区也搜得到', () => {
    expect(searchStatic(ITEMS, '记忆')[0]).toMatchObject({ title: '记忆', act: { kind: 'go', route: { page: 'memory' } } })
    expect(searchStatic(ITEMS, '语音')[0]).toMatchObject({ act: { route: { page: 'settings', section: 'voice' } } })
    expect(searchStatic(ITEMS, '新任务')[0].act).toEqual({ kind: 'new-task' })
    expect(searchStatic(ITEMS, '没有这种东西')).toEqual([])
  })

  it('搜聊过的话（新的在前）和记忆，空格分开的词都要有', () => {
    const list = [msg('a', '周末去杭州爬山'), msg('b', '[happy]杭州的天气不错'), msg('c', '上海', true)]
    const found = historyItems(list, '杭州')
    expect(found.map((i) => i.id)).toEqual(['chat:b', 'chat:a'])
    expect(found[0].title).toBe('杭州的天气不错') // 标签去掉
    expect(found[0].act).toEqual({ kind: 'chat', query: '杭州' })
    expect(historyItems(list, '杭州 爬山').map((i) => i.id)).toEqual(['chat:a'])
    const mems = [{ id: 'm', content: '用户在杭州工作', kind: 'profile' } as StoredMemory]
    expect(memoryItems(mems, '杭州')[0]).toMatchObject({ group: '记忆', act: { route: { page: 'memory' } } })
  })

  it('长的话只截找到的那一段', () => {
    const long = `${'前'.repeat(60)}关键${'后'.repeat(60)}`
    const s = snippet(long, '关键', 5)
    expect(s).toBe('…前前前前前关键后后后后后…')
  })
})

function msg(id: string, content: string, deleted = false): StoredMessage {
  return { id, role: 'user', content, createdAt: `2026-11-2${id.charCodeAt(0) - 96}T10:00:00Z`, deleted }
}
