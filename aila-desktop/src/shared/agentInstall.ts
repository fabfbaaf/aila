/** 设置页展示的官方安装地址。主进程只从这张表取链接，窗口只需传 Agent ID。 */
import { isAgentEngine, type AgentEngine } from './agent'

const installUrls: Readonly<Record<AgentEngine, string>> = {
  codex: 'https://learn.chatgpt.com/docs/cli',
  claude: 'https://code.claude.com/docs/en/quickstart',
  antigravity: 'https://antigravity.google/docs/getting-started?tab=cli',
  codebuddy: 'https://www.codebuddy.cn/docs/cli/quickstart',
  copilot: 'https://docs.github.com/en/copilot/how-tos/copilot-cli/set-up-copilot-cli/install-copilot-cli',
  kimi: 'https://github.com/MoonshotAI/kimi-code#install',
  opencode: 'https://opencode.ai/docs/#install',
  cursor: 'https://prod.cursor.com/docs/cli/installation',
  qwen: 'https://qwenlm.github.io/qwen-code-docs/en/users/quickstart/',
  minimax: 'https://github.com/MiniMax-AI/minimax-code',
  'codex-alt': 'https://learn.chatgpt.com/docs/cli',
  'claude-alt': 'https://code.claude.com/docs/en/quickstart'
}

export function agentInstallUrl(id: unknown): string | undefined {
  return isAgentEngine(id) ? installUrls[id] : undefined
}
