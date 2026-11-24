import { describe, expect, it } from 'vitest'
import { MEMORY_MAX, cleanMemoryEdit } from './cloud'

describe('记忆页里自己加、改的一条', () => {
  it('整理成操作：空白收成一个，日程带日期', () => {
    expect(cleanMemoryEdit({ op: 'add', content: '  我在\n杭州工作 ', kind: 'profile' })).toEqual({
      op: 'add', content: '我在 杭州工作', kind: 'profile', expiresAt: null
    })
    const ev = cleanMemoryEdit({ op: 'update', id: 'm1', content: '周五交周报', kind: 'event', expiresAt: '2026-11-27T23:59:59+08:00' })
    expect(ev).toMatchObject({ op: 'update', id: 'm1', kind: 'event' })
    expect((ev as { expiresAt: string | null }).expiresAt).toBe(new Date('2026-11-27T15:59:59Z').toISOString())
    expect(cleanMemoryEdit({ op: 'add', content: '随便', kind: 'nope' })).toMatchObject({ kind: 'other' })
  })

  it('不对的告诉你哪里不对', () => {
    expect(cleanMemoryEdit({ op: 'add', content: '   ' })).toBe('写点什么再保存。')
    expect(cleanMemoryEdit({ op: 'add', content: '字'.repeat(MEMORY_MAX + 1) })).toContain(`${MEMORY_MAX}`)
    expect(cleanMemoryEdit({ op: 'add', content: '好', expiresAt: 'not a date' })).toBe('日期不对。')
    expect(cleanMemoryEdit({ op: 'update', content: '好' })).toBe('内容不对。') // 改要有 id
    expect(cleanMemoryEdit({ op: 'delete', id: 'x', content: '好' })).toBe('内容不对。') // 删走别的路
    expect(cleanMemoryEdit(null)).toBe('内容不对。')
  })
})
