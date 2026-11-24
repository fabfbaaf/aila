import { describe, expect, it } from 'vitest'
import { cleanLive } from './chat'

describe('转给主窗口的回复进度', () => {
  it('只认这几个字段，长度有上限', () => {
    const r = cleanLive({ phase: 'streaming', text: '好', status: '', error: '', ask: { id: 'c1', skill: '浏览器', action: '打开', detail: 'x'.repeat(9000), evil: 1 }, extra: 'x' })
    expect(r).toEqual({ phase: 'streaming', text: '好', status: '', error: '', ask: { id: 'c1', skill: '浏览器', action: '打开', detail: 'x'.repeat(4000) } })
    expect(cleanLive({ phase: 'hack', text: '' })).toBeNull()
    expect(cleanLive(null)).toBeNull()
    expect(cleanLive({ phase: 'done', text: 3, ask: { id: '' } })).toEqual({ phase: 'done', text: '', status: '', error: '', ask: null })
  })
})
