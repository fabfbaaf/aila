/** 交付卡和验收是主窗口的操作：只认主窗口，会话 id、验收内容先在这边检查一遍再转给服务。 */
import type { IpcMain, WebContents } from 'electron'
import type { AgentRelay } from './agent'
import { isGithubLink, validAcceptance } from '../shared/delivery'
import { tm } from './lang'

export function registerDeliveryIpc(
  ipc: Pick<IpcMain, 'handle'>,
  relay: Pick<AgentRelay, 'delivery' | 'accept'>,
  fromMain: (event: { sender: WebContents }) => boolean,
  sidOk: (sid: unknown) => sid is string,
  openExternal: (url: string) => Promise<void>
): void {
  ipc.handle('agent:delivery', (event, sid: unknown, ci: unknown) => {
    if (!fromMain(event) || !sidOk(sid)) return { ok: false, error: tm('只有主窗口能用') }
    return relay.delivery(sid, ci === true)
  })
  ipc.handle('agent:accept', (event, sid: unknown, verdict: unknown, note: unknown, comments: unknown) => {
    if (!fromMain(event) || !sidOk(sid)) return { ok: false, error: tm('只有主窗口能用') }
    if (!validAcceptance(verdict, note, comments)) return { ok: false, error: tm('验收的内容不对。') }
    return relay.accept(sid, verdict as 'accepted' | 'rejected', note as string, comments)
  })
  // 交付卡上的 PR、CI 链接：只开 GitHub 上某个仓库里的页面，用系统浏览器
  ipc.handle('agent:open-github', async (event, url: unknown) => {
    if (!fromMain(event) || !isGithubLink(url)) return { ok: false, error: tm('只能打开 GitHub 上的链接。') }
    try {
      await openExternal(url)
      return { ok: true }
    } catch {
      return { ok: false, error: tm('没打开浏览器。') }
    }
  })
}
