/** 托管服务的 IPC：状态可读，日志和重启只给主窗口；不改变已有通道的授权。 */
import type { IpcMain, WebContents } from 'electron'
import type { ServiceKeeper } from './service'

export function registerServiceIpc(ipc: Pick<IpcMain, 'handle'>, keeper: ServiceKeeper, fromMain: (event: { sender: WebContents }) => boolean): void {
  ipc.handle('service:status', () => keeper.status())
  ipc.handle('service:logs', (event) => fromMain(event) ? { ok: true, data: keeper.logs() } : { ok: false, error: '只有主窗口能用' })
  ipc.handle('service:restart', (event) => fromMain(event) ? keeper.restart() : { ok: false, error: '只有主窗口能用' })
}
