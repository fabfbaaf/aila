/** 电脑设置只传开关，不上传环境预览内容；两种运行方式都固定接口。 */
import { afterEach, describe, expect, it, vi } from 'vitest'

afterEach(() => { vi.unstubAllGlobals(); vi.resetModules() })

describe('电脑设置来源', () => {
  it('浏览器保存 false 不丢失，预览固定 GET；服务错误留给界面', async () => {
    vi.stubGlobal('window', {})
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ computer: { inventory: false } })))
    vi.stubGlobal('fetch', fetchMock)
    const api = await import('./computer')
    await api.loadComputerSettings()
    expect(fetchMock).toHaveBeenLastCalledWith('/ai/settings', expect.objectContaining({ method: 'GET' }))
    await api.saveComputerInventory(false)
    expect(fetchMock).toHaveBeenLastCalledWith('/ai/settings', expect.objectContaining({
      method: 'PUT', body: JSON.stringify({ computerInventory: false })
    }))
    await api.loadComputerContext()
    expect(fetchMock).toHaveBeenLastCalledWith('/ai/agent/computer/context', expect.objectContaining({ method: 'GET', body: undefined }))
    fetchMock.mockResolvedValueOnce(new Response('{"detail":"暂时不可用"}', { status: 503 }))
    expect(await api.loadComputerContext()).toEqual({ ok: false, error: '暂时不可用' })
  })

  it('桌面用固定的设置与预览方法，读取失败能返回离线信息', async () => {
    const bridge = {
      aiSettings: vi.fn(async () => ({ ok: true })),
      aiSettingsPut: vi.fn(async () => ({ ok: true })),
      agentComputerContext: vi.fn(async () => ({ ok: true }))
    }
    vi.stubGlobal('window', { aila: bridge })
    const api = await import('./computer')
    await api.loadComputerSettings()
    await api.saveComputerInventory(true)
    await api.loadComputerContext()
    expect(bridge.aiSettings).toHaveBeenCalledOnce()
    expect(bridge.aiSettingsPut).toHaveBeenCalledWith({ computerInventory: true })
    expect(bridge.agentComputerContext).toHaveBeenCalledOnce()
  })
})
