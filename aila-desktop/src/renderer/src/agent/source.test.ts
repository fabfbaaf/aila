/** 浏览器与桌面来源都保留零上限，固定请求预算接口。 */
import { afterEach, describe, expect, it, vi } from 'vitest'

afterEach(() => { vi.unstubAllGlobals(); vi.resetModules() })

describe('任务页预算来源', () => {
  it('浏览器经本机代理设置预算，会话路径编码并保留 null', async () => {
    vi.stubGlobal('window', {})
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ type: 'budget', seq: 2, amount: null })))
    vi.stubGlobal('fetch', fetchMock)
    const { createAgentSource } = await import('./source')
    const source = createAgentSource()
    await source.create('C:/shop', 'ask', false, 0)
    expect(fetchMock).toHaveBeenLastCalledWith('/ai/agent/sessions', expect.objectContaining({
      method: 'POST', body: JSON.stringify({ cwd: 'C:/shop', mode: 'ask', isolate: false, budget: 0, kind: 'project', engine: null, model: '' })
    }))
    expect((await source.setBudget('s /1', null)).amount).toBeNull()
    expect(fetchMock).toHaveBeenLastCalledWith('/ai/agent/sessions/s%20%2F1/budget', expect.objectContaining({
      method: 'PUT', body: JSON.stringify({ amount: null })
    }))
  })

  it('桌面经预加载预算方法，IPC 拒绝时把原因留给表单显示', async () => {
    const agentCreate = vi.fn(async () => ({ ok: true, data: { id: 's1' } }))
    const agentSetBudget = vi.fn(async () => ({ ok: false, error: '上限没有保存' }))
    vi.stubGlobal('window', { aila: { agentCreate, agentSetBudget } })
    const { createAgentSource } = await import('./source')
    const source = createAgentSource()
    await source.create('C:/shop', 'ask', false, 0)
    expect(agentCreate).toHaveBeenCalledWith('C:/shop', 'ask', false, 0, 'project', null, '', [])
    await expect(source.setBudget('s1', 0.5)).rejects.toThrow('上限没有保存')
    expect(agentSetBudget).toHaveBeenCalledWith('s1', 0.5)
  })

  it('浏览器创建电脑任务时没有项目路径，长任务仍沿用 plan 参数', async () => {
    vi.stubGlobal('window', {})
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ id: 'c1', kind: 'computer' })))
    vi.stubGlobal('fetch', fetchMock)
    const { createAgentSource } = await import('./source')
    const source = createAgentSource()
    await source.create(undefined, 'ask', false, null, 'computer')
    expect(fetchMock).toHaveBeenLastCalledWith('/ai/agent/sessions', expect.objectContaining({
      method: 'POST', body: JSON.stringify({ mode: 'ask', isolate: false, budget: null, kind: 'computer', engine: null, model: '' })
    }))
    source.send('c1', '整理测试文件夹', false, true)
    expect(fetchMock).toHaveBeenLastCalledWith('/ai/agent/sessions/c1/messages', expect.objectContaining({
      body: JSON.stringify({ text: '整理测试文件夹', plan: true })
    }))
  })
})
