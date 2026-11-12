import { streamChat, type ChatEvent, type ChatMessage } from '../../../shared/chat'
import type { AilaApi } from '../../../preload/index'
import { bridge } from '../platform/bridge'

export interface ChatStream {
  cancel(): void
}

/** 把对话历史发给本地 AI 服务，事件按顺序回调，最后一定是 done 或 error（取消后不再回调） */
export interface ChatTransport {
  start(messages: ChatMessage[], onEvent: (ev: ChatEvent) => void): ChatStream
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
    }
  }
}

/** 浏览器调试：经 Vite 代理 /ai 转到本地 AI 服务 */
function webTransport(): ChatTransport {
  return {
    start(messages, onEvent) {
      const controller = new AbortController()
      void streamChat('/ai', messages, controller.signal, onEvent)
      return { cancel: () => controller.abort() }
    }
  }
}

export function createTransport(): ChatTransport {
  return bridge ? desktopTransport(bridge) : webTransport()
}
