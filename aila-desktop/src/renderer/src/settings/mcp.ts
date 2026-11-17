/** 设置页 → 编程 → 外部工具（MCP）的请求：桌面走只认主窗口的 IPC，浏览器调试走 Vite 的本机服务代理。 */
import type { McpPreviewItem, McpServerInfo } from '../../../shared/mcp'
import type { AiResult } from '../../../shared/settings'
import { bridge } from '../platform/bridge'

async function request<T>(method: string, path: string, body?: unknown, timeout = 30000): Promise<AiResult<T>> {
  try {
    const res = await fetch(`/ai/agent/mcp${path}`, {
      method,
      headers: body === undefined ? undefined : { 'content-type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(timeout)
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) return { ok: false, error: typeof data.detail === 'string' ? data.detail : `AI 服务返回 ${res.status}` }
    return { ok: true, data: data as T }
  } catch {
    return { ok: false, error: '连不上 AI 服务，启动服务后再重试。' }
  }
}

const name = (n: string): string => `/${encodeURIComponent(n)}`

export const loadMcp = (): Promise<AiResult<McpServerInfo[]>> => (bridge ? bridge.agentMcp() : request('GET', ''))

export const previewMcp = (json: string): Promise<AiResult<McpPreviewItem[]>> =>
  bridge ? bridge.agentMcpPreview(json) : request('POST', '/preview', { json })

export const importMcp = (json: string): Promise<AiResult<McpServerInfo[]>> =>
  bridge ? bridge.agentMcpImport(json) : request('POST', '/import', { json }, 180000)

export const restartMcp = (n: string): Promise<AiResult<McpServerInfo[]>> =>
  bridge ? bridge.agentMcpRestart(n) : request('POST', `${name(n)}/restart`, undefined, 120000)

export const disableMcp = (n: string, disabled: boolean): Promise<AiResult<McpServerInfo[]>> =>
  bridge ? bridge.agentMcpDisable(n, disabled) : request('PUT', `${name(n)}/disabled`, { disabled }, 120000)

export const removeMcp = (n: string): Promise<AiResult<McpServerInfo[]>> =>
  bridge ? bridge.agentMcpRemove(n) : request('DELETE', name(n))

export const forgetMcpTool = (n: string, tool: string): Promise<AiResult<McpServerInfo[]>> =>
  bridge ? bridge.agentMcpForget(n, tool) : request('DELETE', `${name(n)}/allow`, { tool })

/** 例子：官方的文件系统服务（只给一个目录） */
export const MCP_EXAMPLE = `{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "C:/Users/你/Documents"]
    }
  }
}`
