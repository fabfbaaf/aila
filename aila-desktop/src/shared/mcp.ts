/** MCP 外部工具服务：AI 服务 /agent/mcp 接口的数据（主进程、预加载、设置页共用） */

export type McpStatus = 'ready' | 'starting' | 'failed' | 'disabled' | 'unsupported' | 'off'

export interface McpToolInfo {
  name: string
  fullName: string
  description: string
  /** 服务标了只读：她用的时候不问 */
  readOnly: boolean
  /** 你选过「这个工具以后都不用问」 */
  alwaysAllow: boolean
}

export interface McpServerInfo {
  name: string
  /** 显示名（配置里的 name，和对话那边的技能包同名；没有就是 name） */
  label: string
  /** 你给整个服务标了只读：工具都不问 */
  readOnly: boolean
  /** 启动命令（由 AI 服务从配置拼出来；不含环境变量的值） */
  command: string
  /** 给这个服务的环境变量：只有名字，值（常常是密钥）不回传 */
  envKeys: string[]
  /** 扩展包带的服务：扩展 id（在设置的「扩展」里管，这里不列） */
  extension?: string | null
  disabled: boolean
  status: McpStatus
  error: string
  server: { name?: string; version?: string }
  tools: McpToolInfo[]
  log: string[]
}

/** 贴进来的配置保存前先看一眼 */
export interface McpPreviewItem {
  name: string
  command: string
  envKeys: string[]
  /** 已经有同名的：保存会覆盖（没给的环境变量保留原来的） */
  exists: boolean
  /** 远程（url）服务暂不支持 */
  supported: boolean
}

export const MCP_STATUS_LABELS: Record<McpStatus, string> = {
  ready: '在运行',
  starting: '正在启动',
  failed: '没起来',
  disabled: '停用了',
  unsupported: '不支持',
  off: '没启动'
}

/** 服务名：字母、数字、下划线、减号，最多 32 个字（和 AI 服务一样） */
export function validMcpName(name: unknown): name is string {
  return typeof name === 'string' && /^[A-Za-z0-9_-]{1,32}$/.test(name)
}
