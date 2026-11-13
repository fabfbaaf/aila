import { answerConfirm, streamChat, type ChatEvent, type ChatMessage } from '../../../shared/chat'
import type { AilaApi } from '../../../preload/index'
import { bridge } from '../platform/bridge'

export interface ChatStream {
  cancel(): void
}

/** 把对话历史发给本地 AI 服务，事件按顺序回调，最后一定是 done 或 error（取消后不再回调） */
export interface ChatTransport {
  start(messages: ChatMessage[], onEvent: (ev: ChatEvent) => void): ChatStream
  /** 回答她的确认；false 表示这个确认已经结束了 */
  confirm(id: string, approved: boolean): Promise<boolean>
  /** 一句话转语音，返回 WAV；没有能念的内容时返回 null，服务不可用时抛错 */
  synthesize(text: string): Promise<ArrayBuffer | null>
  /** 语音（耳朵）的 WebSocket 地址 */
  voiceUrl: string
  /** 看屏幕找报错的 WebSocket 地址 */
  screenUrl: string
}

/** 桌面版：请求由主进程发出再转发回来 */
function desktopTransport(api: AilaApi): ChatTransport {
  let nextId = 1
  const handlers = new Map<number, (ev: ChatEvent) => void>()
  api.onChatEvent((id, ev) => {
    const handler = handlers.get(id)
    if (!handler) return // 已经取消的请求，晚到的事件直接丢掉
    if (ev.type === 'done' || ev.type === 'error') handlers.delete(id)
    handler(ev)
  })

  return {
    start(messages, onEvent) {
      const id = nextId++
      handlers.set(id, onEvent)
      api.chatStart(id, messages)
      return {
        cancel: () => {
          handlers.delete(id)
          api.chatCancel(id)
        }
      }
    },
    confirm: (id, approved) => api.chatConfirm(id, approved),
    synthesize: (text) => api.synthesize(text),
    // WebSocket 不受跨域限制，CSP 也允许 ws:，渲染层直接连；服务端会检查 Origin
    voiceUrl: api.aiUrl.replace(/^http/, 'ws') + '/voice',
    screenUrl: api.aiUrl.replace(/^http/, 'ws') + '/screen'
  }
}

/** 浏览器调试：经 Vite 代理 /ai 转到本地 AI 服务 */
function webTransport(): ChatTransport {
  return {
    start(messages, onEvent) {
      const controller = new AbortController()
      void streamChat('/ai', messages, controller.signal, onEvent)
      return { cancel: () => controller.abort() }
    },
    confirm: (id, approved) => answerConfirm('/ai', id, approved),
    synthesize: async (text) => {
      const res = await fetch('/ai/tts', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ text })
      })
      if (res.status === 204) return null
      if (!res.ok) throw new Error(`语音合成返回 ${res.status}`)
      return res.arrayBuffer()
    },
    voiceUrl: `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ai/voice`,
    screenUrl: `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ai/screen`
  }
}

export function createTransport(): ChatTransport {
  return bridge ? desktopTransport(bridge) : webTransport()
}
