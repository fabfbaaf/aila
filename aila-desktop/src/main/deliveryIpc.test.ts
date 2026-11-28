/** 主进程拒绝角色窗口和不像样的验收，只转发给固定的接口。 */
import { describe, expect, it, vi } from 'vitest'
import { registerDeliveryIpc } from './deliveryIpc'

describe('交付卡 IPC', () => {
  it('只认主窗口和像样的验收', async () => {
    const main = {}
    const callbacks = new Map<string, (...args: any[]) => any>()
    const delivery = vi.fn(async () => ({ ok: true }))
    const accept = vi.fn(async () => ({ ok: true }))
    const open = vi.fn(async () => undefined)
    registerDeliveryIpc(
      { handle: (name, callback) => { callbacks.set(name, callback) } },
      { delivery, accept },
      (event) => event.sender === main,
      (sid): sid is string => typeof sid === 'string' && /^[\w-]{1,64}$/.test(sid),
      open
    )
    const link = callbacks.get('agent:open-github')!
    for (const bad of ['https://evil.example/o/r/pull/1', 'http://github.com/o/r/pull/1', 'https://github.com.evil.example/o/r', 'https://x@github.com/o/r/pull/1',
      'file:///C:/x', 'https://github.com/', 42]) {
      expect(await link({ sender: main }, bad)).toMatchObject({ ok: false })
    }
    expect(await link({ sender: {} }, 'https://github.com/o/r/pull/1')).toMatchObject({ ok: false })
    expect(open).not.toHaveBeenCalled()
    expect(await link({ sender: main }, 'https://github.com/o/r/pull/1')).toEqual({ ok: true })
    expect(open).toHaveBeenLastCalledWith('https://github.com/o/r/pull/1')
    const card = callbacks.get('agent:delivery')!
    const verdict = callbacks.get('agent:accept')!
    expect(await card({ sender: {} }, 's1', false)).toMatchObject({ ok: false })
    expect(await card({ sender: main }, '../x', false)).toMatchObject({ ok: false })
    expect(await card({ sender: main }, 's1', 'yes')).toEqual({ ok: true })
    expect(delivery).toHaveBeenLastCalledWith('s1', false)
    expect(await verdict({ sender: {} }, 's1', 'accepted', '', [])).toMatchObject({ ok: false })
    expect(await verdict({ sender: main }, 's1', 'merge', '', [])).toMatchObject({ ok: false })
    expect(await verdict({ sender: main }, 's1', 'rejected', '', [{ file: 'a', line: 0, side: 'new', text: 'x' }])).toMatchObject({ ok: false })
    expect(accept).not.toHaveBeenCalled()
    const comments = [{ file: 'a.py', line: 2, side: 'new', text: '改成 2' }]
    expect(await verdict({ sender: main }, 's1', 'rejected', '', comments)).toEqual({ ok: true })
    expect(accept).toHaveBeenLastCalledWith('s1', 'rejected', '', comments)
  })
})
