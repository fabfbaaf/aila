import { contextBridge, ipcRenderer } from 'electron'
import { AI_SERVICE_URL, type ChatEvent, type ChatMessage } from '../shared/chat'
import type { VoiceMenuAction, VoiceMenuState } from '../shared/voice'

// 渲染层能用的全部能力都在这里，渲染层不直接接触 Node 和 Electron
const api = {
  setIgnoreMouse: (ignore: boolean): void => ipcRenderer.send('win:set-ignore-mouse', ignore),
  focusWindow: (): void => ipcRenderer.send('win:focus'),
  blurWindow: (): void => ipcRenderer.send('win:blur'),
  dragStart: (): void => ipcRenderer.send('win:drag-start'),
  dragMove: (): void => ipcRenderer.send('win:drag-move'),
  dragEnd: (): void => ipcRenderer.send('win:drag-end'),
  showMenu: (voice: VoiceMenuState): void => ipcRenderer.send('menu:show', voice),
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
  },

  // 语音
  /** 本地 AI 服务地址（语音 WebSocket 由渲染层直接连） */
  aiUrl: process.env['AILA_AI_URL'] ?? AI_SERVICE_URL,
  synthesize: (text: string): Promise<ArrayBuffer | null> => ipcRenderer.invoke('tts:synthesize', text),
  onVoiceMenu: (cb: (action: VoiceMenuAction) => void): void => {
    ipcRenderer.on('voice:menu', (_e, action) => cb(action))
  }
}

export type AilaApi = typeof api

contextBridge.exposeInMainWorld('aila', api)
