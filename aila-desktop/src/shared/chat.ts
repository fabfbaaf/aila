// 主进程和渲染层共用：对话消息、AI 服务推过来的事件，以及 SSE 解析

export type Emotion = 'happy' | 'sad' | 'surprised' | 'relaxed' | 'neutral'
/** wave 挥手（大模型也能用）；stretch 伸懒腰（只在本地用：久坐提醒、睡醒） */
export type BuiltinGesture = 'nod' | 'shake' | 'tilt' | 'think' | 'bow' | 'wave' | 'stretch'
export const BUILTIN_GESTURES: readonly BuiltinGesture[] = ['nod', 'shake', 'tilt', 'think', 'bow', 'wave', 'stretch']
/** 内置的，或者扩展包带的动作名（见 shared/extensions.ts；不认识的名字播放时忽略） */
export type Gesture = BuiltinGesture | (string & {})

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
}

/** offline：连不上本地 AI 服务；其余由 AI 服务归类 */
export type ChatErrorCode = 'offline' | 'auth' | 'rate_limit' | 'quota' | 'network' | 'unknown'

/** 要用户点头的操作。detail 由 AI 服务从工具参数生成（比如完整网址），不是大模型自己的描述 */
export interface ConfirmAsk {
  id: string
  /** 哪个技能包，比如「浏览器」 */
  skill: string
  /** 要做的事，她会问「要我{action}吗？」 */
  action: string
  detail: string
}

export type ConfirmResult = 'approved' | 'rejected' | 'timeout'

export type ChatEvent =
  /** spoken 省略时按旧版服务的行为，把文字照常念出来。more 是桌面端自己的提示。 */
  | { type: 'text'; text: string; spoken?: boolean; hint?: 'more' }
  | { type: 'emotion'; name: Emotion }
  | { type: 'action'; name: Gesture }
  /** 她在用工具（搜索、读网页），text 是给用户看的进度，比如「正在搜索「xxx」」 */
  | { type: 'status'; text: string }
  /** 会改东西的操作（打开网页、发消息……）：回复暂停，等用户回答（POST /chat/confirm） */
  | ({ type: 'confirm' } & ConfirmAsk)
  | { type: 'confirm_end'; id: string; result: ConfirmResult }
  /** 她把编程任务交给了工作窗口（对话里的「编程任务」工具）：桌面端打开工作窗口接上这个会话 */
  | { type: 'agent_task'; session: string; project: string }
  /** 她记下了一个提醒（对话里的「提醒」技能包）：主进程存起来，到点叫你。at 是带时区的 ISO 时间 */
  | { type: 'reminder'; at: string; text: string }
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

/** 回答一次确认。返回 false：这个确认已经结束了（超时、回复被取消），或者连不上 AI 服务 */
export async function answerConfirm(baseUrl: string, id: string, approved: boolean): Promise<boolean> {
  try {
    const res = await fetch(`${baseUrl}/chat/confirm`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ id, approved })
    })
    return res.ok
  } catch {
    return false
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
  onEvent: (ev: ChatEvent) => void,
  /** 一起发给 AI 服务的其他内容（她记得的事、开着的技能包） */
  extra: Record<string, unknown> = {}
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
      body: JSON.stringify({ ...extra, messages }),
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
