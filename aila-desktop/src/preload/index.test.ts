// 预加载仅把会话管理参数转给主进程，不向渲染层暴露任意 HTTP 请求。
import { describe, expect, it, vi } from 'vitest'
import { contextBridge, ipcRenderer } from 'electron'
import type { AilaApi } from './index'
import './index'

vi.mock('electron', () => ({
  contextBridge: { exposeInMainWorld: vi.fn() },
  ipcRenderer: { invoke: vi.fn(async () => ({ ok: true })), on: vi.fn(), removeListener: vi.fn(), send: vi.fn() }
}))

describe('会话管理的预加载桥', () => {
  it('撤销只转发会话和已确认的轮次', async () => {
    const exposed = vi.mocked(contextBridge.exposeInMainWorld).mock.calls.find(([key]) => key === 'aila')
    const api = exposed?.[1] as AilaApi
    await api.agentUndo('s1', 4)
    expect(ipcRenderer.invoke).toHaveBeenLastCalledWith('agent:undo', 's1', 4)
  })

  it('改名和删除只调用各自的固定 IPC', async () => {
    const exposed = vi.mocked(contextBridge.exposeInMainWorld).mock.calls.find(([key]) => key === 'aila')
    const api = exposed?.[1] as AilaApi
    expect(api).toBeDefined()
    await api.agentRename('s1', '新标题')
    expect(ipcRenderer.invoke).toHaveBeenLastCalledWith('agent:rename', 's1', '新标题')
    await api.agentDelete('s1')
    expect(ipcRenderer.invoke).toHaveBeenLastCalledWith('agent:delete', 's1')
  })
})
