import { describe, expect, it } from 'vitest'
import { agentInstallUrl } from './agentInstall'

describe('本机 Agent 官方安装地址', () => {
  it('只接受已知 Agent 的 ID', () => {
    expect(agentInstallUrl('codex')).toBe('https://learn.chatgpt.com/docs/cli')
    expect(agentInstallUrl('claude')).toBe('https://code.claude.com/docs/en/quickstart')
    expect(agentInstallUrl('antigravity')).toBe('https://antigravity.google/docs/getting-started?tab=cli')
    expect(agentInstallUrl('codebuddy')).toBe('https://www.codebuddy.cn/docs/cli/quickstart')
    expect(agentInstallUrl('https://example.com')).toBeUndefined()
    expect(agentInstallUrl('__proto__')).toBeUndefined()
    expect(agentInstallUrl('constructor')).toBeUndefined()
    expect(agentInstallUrl(null)).toBeUndefined()
  })
})
