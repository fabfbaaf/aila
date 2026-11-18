/** 新建电脑任务的 IPC 校验不依赖渲染层，拒绝越权窗口和不支持的任务参数。 */
import { describe, expect, it, vi } from 'vitest'
import { registerAgentCreateIpc } from './agentCreateIpc'

function setup() {
  const main = {}
  const callbacks = new Map<string, (...args: any[]) => any>()
  const relay = { create: vi.fn(async () => ({ ok: true })), computerContext: vi.fn(async () => ({ ok: true })) }
  registerAgentCreateIpc({ handle: (name, callback) => { callbacks.set(name, callback) } }, relay, (event) => event.sender === main)
  return { relay, main: { sender: main }, create: callbacks.get('agent:create')!, preview: callbacks.get('agent:computer-context')! }
}

describe('电脑任务 IPC', () => {
  it('只有主窗口能创建和看环境；电脑拒绝目录、自动权限和新分支，预算保持原值', async () => {
    const { relay, main, create, preview } = setup()
    expect(await create({ sender: {} }, undefined, 'ask', false, null, 'computer')).toMatchObject({ ok: false })
    expect(await preview({ sender: {} })).toMatchObject({ ok: false })
    for (const [cwd, mode, isolate, budget, kind] of [
      ['C:/folder', 'ask', false, null, 'computer'],
      [undefined, 'auto', false, null, 'computer'],
      [undefined, 'read_only', true, null, 'computer'],
      [undefined, 'ask', false, -1, 'computer'],
      [undefined, 'ask', false, Infinity, 'computer'],
      [undefined, 'ask', false, null, 'unknown']
    ]) expect(await create(main, cwd, mode, isolate, budget, kind)).toMatchObject({ ok: false })
    expect(relay.create).not.toHaveBeenCalled()
    expect(relay.computerContext).not.toHaveBeenCalled()
    expect(await create(main, undefined, 'ask', false, 0, 'computer')).toEqual({ ok: true })
    expect(relay.create).toHaveBeenLastCalledWith(undefined, 'ask', false, 0, 'computer')
    await create(main, undefined, 'read_only', false, undefined, 'computer')
    expect(relay.create).toHaveBeenLastCalledWith(undefined, 'read_only', false, null, 'computer')
    await preview(main)
    expect(relay.computerContext).toHaveBeenCalledOnce()
  })

  it('旧调用没有 kind 时保持项目的模式和分支参数', async () => {
    const { relay, main, create } = setup()
    await create(main, 'C:/project', 'auto', true, null)
    expect(relay.create).toHaveBeenLastCalledWith('C:/project', 'auto', true, null, 'project')
  })
})
