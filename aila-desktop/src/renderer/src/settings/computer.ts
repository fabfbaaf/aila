/** 电脑环境设置和预览：桌面用固定 IPC，浏览器调试用本机代理，不向渲染层暴露任意请求。 */
import type { ComputerContext } from '../../../shared/agent'
import type { AiResult, AiSettings } from '../../../shared/settings'
import { bridge } from '../platform/bridge'
import { t } from '../i18n'

async function request<T>(path: '/settings' | '/agent/computer/context', inventory?: boolean): Promise<AiResult<T>> {
  try {
    const res = await fetch(`/ai${path}`, {
      method: inventory === undefined ? 'GET' : 'PUT',
      headers: inventory === undefined ? undefined : { 'content-type': 'application/json' },
      body: inventory === undefined ? undefined : JSON.stringify({ computerInventory: inventory }),
      signal: AbortSignal.timeout(10000)
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) return { ok: false, error: typeof data.detail === 'string' ? data.detail : `AI 服务返回 ${res.status}` }
    return { ok: true, data: data as T }
  } catch {
    return { ok: false, error: t('连不上 AI 服务，启动服务后再重试。') }
  }
}

export const loadComputerSettings = (): Promise<AiResult<AiSettings>> =>
  bridge ? bridge.aiSettings() : request('/settings')

export const saveComputerInventory = (inventory: boolean): Promise<AiResult<AiSettings>> =>
  bridge ? bridge.aiSettingsPut({ computerInventory: inventory }) : request('/settings', inventory)

export const loadComputerContext = (): Promise<AiResult<ComputerContext>> =>
  bridge ? bridge.agentComputerContext() : request('/agent/computer/context')
