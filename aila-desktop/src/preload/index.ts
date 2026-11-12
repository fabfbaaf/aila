import { contextBridge, ipcRenderer } from 'electron'
import type { ChatEvent, ChatMessage } from '../shared/chat'

// 渲染层能用的全部能力都在这里，渲染层不直接接触 Node 和 Electron
const api = {
  setIgnoreMouse: (ignore: boolean): void => ipcRenderer.send('win:set-ignore-mouse', ignore),
  focusWindow: (): void => ipcRenderer.send('win:focus'),
  blurWindow: (): void => ipcRenderer.send('win:blur'),
  dragStart: (): void => ipcRenderer.send('win:drag-start'),
  dragMove: (): void => ipcRenderer.send('win:drag-move'),
  dragEnd: (): void => ipcRenderer.send('win:drag-end'),
  showMenu: (): void => ipcRenderer.send('menu:show'),
  getLastModel: (): Promise<ArrayBuffer | null> => ipcRenderer.invoke('model:get-last'),
  onCursor: (cb: (p: { x: number; y: number }) => void): void => {
    ipcRenderer.on('cursor', (_e, p) => cb(p))
  },
  onModelLoaded: (cb: (data: ArrayBuffer) => void): void => {
    ipcRenderer.on('model:loaded', (_e, data) => cb(data))
  },

  // 对话
  chatStart: (id: number, messages: ChatMessage[]): void => ipcRenderer.send('chat:start', id, messages),
  chatCancel: (id: number): void => ipcRenderer.send('chat:cancel', id),
  onChatEvent: (cb: (id: number, ev: ChatEvent) => void): void => {
    ipcRenderer.on('chat:event', (_e, id, ev) => cb(id, ev))
  },
  /** 右键菜单或全局快捷键要求打开对话框 */
  onChatOpen: (cb: () => void): void => {
    ipcRenderer.on('chat:open', () => cb())
  }
}

export type AilaApi = typeof api

contextBridge.exposeInMainWorld('aila', api)
