import { describe, expect, it, vi } from 'vitest'
import { cleanTarget, registerUnderstandIpc, type UnderstandRelay } from './understandIpc'

vi.mock('./lang', () => ({ tm: (s: string) => s }))

function setup() {
  const handlers = new Map<string, (...args: unknown[]) => unknown>()
  const ipc = { handle: (channel: string, fn: (...args: unknown[]) => unknown) => handlers.set(channel, fn) }
  const ok = vi.fn(async () => ({ ok: true, data: {} }))
  const relay: UnderstandRelay = { area: ok, chain: ok, endpoints: ok, file: ok, fn: ok, explain: ok, diagnose: ok }
  const main = { sender: { id: 1 } }
  const other = { sender: { id: 2 } }
  registerUnderstandIpc(ipc as never, relay, (e) => (e.sender as unknown as { id: number }).id === 1)
  const call = (channel: string, event: unknown, ...args: unknown[]) => handlers.get(channel)!(event, ...args)
  return { call, ok, main, other }
}

describe('看懂项目的 IPC', () => {
  it('只认主窗口', async () => {
    const { call, ok, other } = setup()
    expect(await call('code:explain', other, 'p1', { level: 'area', depth: 'dev' })).toEqual({ ok: false, error: '只有主窗口能用' })
    expect(await call('code:area', other, 'p1', '')).toMatchObject({ ok: false })
    expect(ok).not.toHaveBeenCalled()
  })

  it('参数类型、长度不对就挡下', async () => {
    const { call, ok, main } = setup()
    expect(await call('code:area', main, 'p1', 42)).toEqual({ ok: false, error: '参数不对。' })
    expect(await call('code:chain', main, 'p1', '', '')).toMatchObject({ ok: false }) // 起点和接口都没有
    expect(await call('code:function', main, 'p1', 'a.py', 0)).toMatchObject({ ok: false })
    expect(await call('code:diagnose', main, 'p1', 'x'.repeat(20001), 'dev')).toMatchObject({ ok: false })
    expect(await call('code:explain', main, 'p1', { level: 'evil' })).toMatchObject({ ok: false })
    expect(ok).not.toHaveBeenCalled()
    await call('code:area', main, 'p1', 'aila-ai', 'yes')
    expect(ok).toHaveBeenLastCalledWith('p1', 'aila-ai', false) // 只有 true 才算要看测试
    await call('code:function', main, 'p1', 'a.py', 12)
    expect(ok).toHaveBeenLastCalledWith('p1', 'a.py', 12)
    await call('code:diagnose', main, 'p1', 'KeyError', 'weird')
    expect(ok).toHaveBeenLastCalledWith('p1', 'KeyError', 'beginner')
  })

  it('讲解目标只留认识的字段', () => {
    expect(cleanTarget({ level: 'function', file: 'a.py', line: 3, depth: 'dev', evil: 'x' }))
      .toEqual({ level: 'function', path: '', file: 'a.py', endpoint: '', line: 3, depth: 'dev' })
    expect(cleanTarget({ level: 'area' })).toEqual({ level: 'area', path: '', file: '', endpoint: '', line: 0, depth: 'beginner' })
    expect(cleanTarget({ level: 'file', line: 1.5 })).toBeNull()
    expect(cleanTarget({ level: 'chain', endpoint: 'x'.repeat(301) })).toBeNull()
    expect(cleanTarget(null)).toBeNull()
  })
})
