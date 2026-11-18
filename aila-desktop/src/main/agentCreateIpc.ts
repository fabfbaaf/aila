/** 新建任务和电脑环境预览只认主窗口；电脑任务不接收项目目录、自动模式或隔离分支。 */
import type { IpcMain, WebContents } from 'electron'
import type { AgentRelay } from './agent'
import { agentModes, type AgentKind, type AgentMode } from '../shared/agent'
import { validBudget } from '../shared/budget'

export function registerAgentCreateIpc(
  ipc: Pick<IpcMain, 'handle'>,
  relay: Pick<AgentRelay, 'create' | 'computerContext'>,
  fromMain: (event: { sender: WebContents }) => boolean
): void {
  const notAllowed = { ok: false, error: '只有主窗口能用' }
  ipc.handle('agent:create', (event, cwd: unknown, mode: unknown, isolate: unknown, budget: unknown, rawKind: unknown) => {
    if (!fromMain(event)) return notAllowed
    const kind = rawKind === undefined ? 'project' : rawKind
    if (kind !== 'project' && kind !== 'computer') return { ok: false, error: '任务类型要是项目或电脑。' }
    if (kind === 'computer') {
      if (cwd !== undefined) return { ok: false, error: '电脑任务不用选项目文件夹。' }
      if (!agentModes('computer').includes(mode as AgentMode)) return { ok: false, error: '电脑任务只能选只读或先问再改。' }
      if (isolate === true) return { ok: false, error: '电脑任务不能在新分支上做。' }
    } else if (typeof cwd !== 'string' || cwd.length > 1000) return notAllowed
    const amount = budget === undefined ? null : budget
    if (!validBudget(amount)) return { ok: false, error: '花费上限要是有限的非负金额，null 表示不限。' }
    const selected = agentModes().includes(mode as AgentMode) ? mode as AgentMode : 'ask'
    return relay.create(cwd as string | undefined, selected, isolate === true, amount, kind as AgentKind)
  })
  ipc.handle('agent:computer-context', (event) => fromMain(event) ? relay.computerContext() : notAllowed)
}
