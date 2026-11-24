/** 编程设置的请求：桌面走有发送方校验的 IPC，浏览器调试走 Vite 的本机服务代理。 */
import type { AgentRuleGroup, AiResult } from '../../../shared/settings'
import { bridge } from '../platform/bridge'
import { t } from '../i18n'

async function request<T>(method: 'GET' | 'DELETE', body?: { project: string; command: string }): Promise<AiResult<T>> {
  try {
    const res = await fetch('/ai/agent/rules', {
      method,
      headers: body ? { 'content-type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
      signal: AbortSignal.timeout(5000)
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) return { ok: false, error: typeof data.detail === 'string' ? data.detail : `AI 服务返回 ${res.status}` }
    return { ok: true, data: data as T }
  } catch {
    return { ok: false, error: t('连不上 AI 服务，启动服务后再重试。') }
  }
}

export const loadAgentRules = (): Promise<AiResult<AgentRuleGroup[]>> =>
  bridge ? bridge.agentRules() : request<AgentRuleGroup[]>('GET')

export const removeAgentRule = (project: string, command: string): Promise<AiResult<unknown>> =>
  bridge ? bridge.agentForgetRule(project, command) : request('DELETE', { project, command })
