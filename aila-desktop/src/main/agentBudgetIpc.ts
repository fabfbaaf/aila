/** 预算是主窗口的写操作，使用与其他 agent 通道相同的发送方和会话校验。 */
import type { IpcMain, WebContents } from 'electron'
import type { AgentRelay } from './agent'
import { validBudget } from '../shared/budget'

export function registerAgentBudgetIpc(
  ipc: Pick<IpcMain, 'handle'>,
  relay: Pick<AgentRelay, 'setBudget'>,
  fromMain: (event: { sender: WebContents }) => boolean,
  sidOk: (sid: unknown) => sid is string
): void {
  ipc.handle('agent:budget', (event, sid: unknown, amount: unknown) => {
    if (!fromMain(event) || !sidOk(sid)) return { ok: false, error: '只有主窗口能用' }
    if (!validBudget(amount)) return { ok: false, error: '花费上限要是有限的非负金额，null 表示不限。' }
    return relay.setBudget(sid, amount)
  })
}
