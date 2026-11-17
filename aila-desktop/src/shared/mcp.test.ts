import { describe, expect, it } from 'vitest'
import { MCP_STATUS_LABELS, validMcpName } from './mcp'

describe('MCP 服务名', () => {
  it('只认字母、数字、下划线、减号，最多 32 个字', () => {
    expect(validMcpName('github')).toBe(true)
    expect(validMcpName('my-db_2')).toBe(true)
    for (const bad of ['', 'a b', '../x', 'a/b', '中文', 'x'.repeat(33), 3, null]) expect(validMcpName(bad)).toBe(false)
  })

  it('每种状态都有中文说法', () => {
    expect(Object.keys(MCP_STATUS_LABELS).sort()).toEqual(['disabled', 'failed', 'off', 'ready', 'starting', 'unsupported'])
  })
})
