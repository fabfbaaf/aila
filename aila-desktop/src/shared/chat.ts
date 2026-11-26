// 主进程和渲染层共用：对话消息、AI 服务推过来的事件，以及 SSE 解析

import type { Spot } from './move'

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

/**
 * 她正在说的这一句回复（主窗口的对话页实时显示用）。对话本身只有一份，在桌面上的她那里跑（出声、动作、记忆都在那）；
 * 角色窗口把进度推给主进程，主进程转给主窗口。回复说完记进对话记录，主窗口从记录里显示
 */
export interface LiveReply {
  phase: 'waiting' | 'streaming' | 'done' | 'error'
  /** 到现在为止的全文（不含表情动作标签） */
  text: string
  /** 正在搜索、阅读时的进度 */
  status: string
  error: string
  /** 正在等你回答的确认（没有是 null） */
  ask: ConfirmAsk | null
}

const LIVE_PHASES = new Set(['waiting', 'streaming', 'done', 'error'])
const str = (v: unknown, max: number): string => (typeof v === 'string' ? v.slice(0, max) : '')

/** 主进程转发之前检查一遍（只认这些字段，长度有上限） */
export function cleanLive(raw: unknown): LiveReply | null {
  if (!raw || typeof raw !== 'object') return null
  const r = raw as Record<string, unknown>
  if (typeof r.phase !== 'string' || !LIVE_PHASES.has(r.phase)) return null
  const a = r.ask as Record<string, unknown> | null | undefined
  const ask = a && typeof a === 'object' && typeof a.id === 'string' && a.id
    ? { id: str(a.id, 64), skill: str(a.skill, 40), action: str(a.action, 200), detail: str(a.detail, 4000) }
    : null
  return { phase: r.phase as LiveReply['phase'], text: str(r.text, 60_000), status: str(r.status, 200), error: str(r.error, 500), ask }
}

export type ChatEvent =
  /** spoken 省略时按旧版服务的行为，把文字照常念出来。more 是桌面端自己的提示。 */
  | { type: 'text'; text: string; spoken?: boolean; hint?: 'more' }
  | { type: 'emotion'; name: Emotion }
  | { type: 'action'; name: Gesture }
  /** 走到屏幕上的某个位置（回复里的 [go-topright] 这类标签） */
  | { type: 'move'; spot: Spot }
  /** 她在用工具（搜索、读网页），text 是给用户看的进度，比如「正在搜索「xxx」」 */
  | { type: 'status'; text: string }
  /** 会改东西的操作（打开网页、发消息……）：回复暂停，等用户回答（POST /chat/confirm） */
  | ({ type: 'confirm' } & ConfirmAsk)
  | { type: 'confirm_end'; id: string; result: ConfirmResult }
  /** 她把编程任务交给了工作窗口（对话里的「编程任务」工具）：桌面端打开工作窗口接上这个会话 */
  | { type: 'agent_task'; session: string; project: string }
  /** 她带你去主窗口的某一页（对话里的「使用说明」技能包）：主进程用 shell.ts 的规则检查后再切 */
  | { type: 'open_page'; route: unknown }
  /** 她记下了一个提醒（对话里的「提醒」技能包）：主进程存起来，到点叫你。at 是带时区的 ISO 时间 */
  | { type: 'reminder'; at: string; text: string }
  | { type: 'done' }
  | { type: 'error'; code: ChatErrorCode; message: string }

export const AI_SERVICE_URL = 'http://127.0.0.1:8765'

/** 认识多久了：第一次见面到现在几天（从 0 算）、你跟她说过几句 */
export interface Bond {
  days: number
  talks: number
}

/** 很熟了（和 aila-ai 的 persona.BOND_STAGES 最后一档一样）：桌面上的台词掺着说孩子气一点的 */
export function isClose(b: Bond | null | undefined): boolean {
  return !!b && b.days >= 14 && b.talks >= 200
}

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
