/** 新建任务和电脑环境预览只认主窗口；电脑任务不接收项目目录、自动模式或隔离分支。 */
import type { IpcMain, WebContents } from 'electron'
import type { AgentRelay } from './agent'
import { agentModes, type AgentEngine, type AgentKind, type AgentMode } from '../shared/agent'
import { validBudget } from '../shared/budget'
import { tm } from './lang'

export function registerAgentCreateIpc(
  ipc: Pick<IpcMain, 'handle'>,
  relay: Pick<AgentRelay, 'create' | 'computerContext'>,
  fromMain: (event: { sender: WebContents }) => boolean
): void {
  const notAllowed = { ok: false, error: tm('只有主窗口能用') }
  ipc.handle('agent:create', (event, cwd: unknown, mode: unknown, isolate: unknown, budget: unknown, rawKind: unknown, rawEngine: unknown) => {
    if (!fromMain(event)) return notAllowed
    const kind = rawKind === undefined ? 'project' : rawKind
    if (kind !== 'project' && kind !== 'computer') return { ok: false, error: tm('任务类型要是项目或电脑。') }
    // 交给本机的 Codex / Claude Code / Antigravity：只认这几个名字，只能在项目里
    const engine = rawEngine === undefined || rawEngine === null ? null : rawEngine
    if (engine !== null && engine !== 'codex' && engine !== 'claude' && engine !== 'antigravity') return { ok: false, error: tm('不认识的 Agent。') }
    if (engine !== null && kind !== 'project') return { ok: false, error: tm('本机 Agent 只能在项目里做。') }
    if (kind === 'computer') {
      if (cwd !== undefined) return { ok: false, error: tm('电脑任务不用选项目文件夹。') }
      if (!agentModes('computer').includes(mode as AgentMode)) return { ok: false, error: tm('电脑任务只能选只读或先问再改。') }
      if (isolate === true) return { ok: false, error: tm('电脑任务不能在新分支上做。') }
    } else if (typeof cwd !== 'string' || cwd.length > 1000) return notAllowed
    const amount = budget === undefined ? null : budget
    if (!validBudget(amount)) return { ok: false, error: tm('花费上限要是有限的非负金额，null 表示不限。') }
    const selected = agentModes().includes(mode as AgentMode) ? mode as AgentMode : 'ask'
    return relay.create(cwd as string | undefined, selected, isolate === true, amount, kind as AgentKind, engine as AgentEngine | null)
  })
  ipc.handle('agent:computer-context', (event) => fromMain(event) ? relay.computerContext() : notAllowed)
}
