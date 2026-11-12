// 主进程和渲染层共用：对话消息、AI 服务推过来的事件，以及 SSE 解析

export type Emotion = 'happy' | 'sad' | 'surprised' | 'relaxed' | 'neutral'
export type Gesture = 'nod' | 'shake' | 'tilt' | 'think' | 'bow'

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
}

/** offline：连不上本地 AI 服务；其余由 AI 服务归类 */
export type ChatErrorCode = 'offline' | 'auth' | 'rate_limit' | 'quota' | 'network' | 'unknown'

export type ChatEvent =
  | { type: 'text'; text: string }
  | { type: 'emotion'; name: Emotion }
  | { type: 'action'; name: Gesture }
  /** 她在用工具（搜索、读网页），text 是给用户看的进度，比如「正在搜索「xxx」」 */
  | { type: 'status'; text: string }
  | { type: 'done' }
  | { type: 'error'; code: ChatErrorCode; message: string }

export const AI_SERVICE_URL = 'http://127.0.0.1:8765'

/** 增量解析 SSE：网络数据块可能在任意位置断开，凑满一个事件（空行结尾）才回调 */
export class SseParser {
  private buf = ''

  constructor(private onData: (data: string) => void) {}

  feed(chunk: string): void {
    this.buf += chunk.replace(/\r/g, '')
    let end: number
    while ((end = this.buf.indexOf('\n\n')) >= 0) {
      const block = this.buf.slice(0, end)
      this.buf = this.buf.slice(end + 2)
      const data = block
        .split('\n')
        .filter((line) => line.startsWith('data:'))
        .map((line) => line.slice(5).replace(/^ /, ''))
        .join('\n')
      if (data) this.onData(data)
    }
  }
}

export function parseChatEvent(data: string): ChatEvent | null {
  try {
    const ev = JSON.parse(data) as ChatEvent
    return typeof ev?.type === 'string' ? ev : null
  } catch {
    return null
  }
}

/**
 * 请求本地 AI 服务并逐个回调事件。保证最后一定回调一次 done 或 error，
 * 调用方不会因为服务挂掉而一直等。主动取消（signal）时什么都不回调。
 */
export async function streamChat(
  baseUrl: string,
  messages: ChatMessage[],
  signal: AbortSignal,
  onEvent: (ev: ChatEvent) => void
): Promise<void> {
  let finished = false
  let connected = false
  const emit = (ev: ChatEvent): void => {
    if (finished || signal.aborted) return
    if (ev.type === 'done' || ev.type === 'error') finished = true
    onEvent(ev)
  }

  try {
    const res = await fetch(`${baseUrl}/chat`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ messages }),
      signal
    })
    if (!res.ok || !res.body) {
      // 502/503/504 是中间的代理连不上 AI 服务
      const unreachable = [502, 503, 504].includes(res.status)
      emit({ type: 'error', code: unreachable ? 'offline' : 'unknown', message: `AI 服务返回 ${res.status}` })
      return
    }
    connected = true

    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    const parser = new SseParser((data) => {
      const ev = parseChatEvent(data)
      if (ev) emit(ev)
    })
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      parser.feed(decoder.decode(value, { stream: true }))
    }
    emit({ type: 'error', code: 'network', message: '回复中途断开了' }) // 正常结束时前面已经收到 done，这句不会生效
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err)
    emit({ type: 'error', code: connected ? 'network' : 'offline', message })
  }
}
