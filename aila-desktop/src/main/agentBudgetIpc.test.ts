/** 主进程拒绝角色窗口和非法预算，只向固定会话预算接口转发。 */
import { describe, expect, it, vi } from 'vitest'
import { registerAgentBudgetIpc } from './agentBudgetIpc'

describe('预算 IPC', () => {
  it('只接受主窗口以及有效会话、有限非负金额或 null', async () => {
    const main = {}
    const callbacks = new Map<string, (...args: any[]) => any>()
    const setBudget = vi.fn(async () => ({ ok: true }))
    registerAgentBudgetIpc(
      { handle: (name, callback) => { callbacks.set(name, callback) } },
      { setBudget },
      (event) => event.sender === main,
      (sid): sid is string => typeof sid === 'string' && /^[\w-]{1,64}$/.test(sid)
    )
    const call = callbacks.get('agent:budget')!
    expect(await call({ sender: {} }, 's1', 1)).toMatchObject({ ok: false })
    expect(await call({ sender: main }, '../x', 1)).toMatchObject({ ok: false })
    for (const amount of [-1, Infinity, NaN, '1', {}, undefined]) {
      expect(await call({ sender: main }, 's1', amount)).toMatchObject({ ok: false })
    }
    expect(setBudget).not.toHaveBeenCalled()
    expect(await call({ sender: main }, 's1', 0)).toEqual({ ok: true })
    expect(setBudget).toHaveBeenLastCalledWith('s1', 0)
    expect(await call({ sender: main }, 's1', null)).toEqual({ ok: true })
    expect(setBudget).toHaveBeenLastCalledWith('s1', null)
  })
})
