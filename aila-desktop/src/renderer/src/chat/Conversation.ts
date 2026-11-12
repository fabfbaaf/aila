import type { ChatErrorCode, ChatEvent, ChatMessage, Emotion, Gesture } from '../../../shared/chat'
import type { Behavior } from '../behavior/Behavior'
import { lines } from '../persona/lines'
import type { ChatStream, ChatTransport } from './transport'

type Item = { kind: 'char'; ch: string } | { kind: 'emotion'; name: Emotion } | { kind: 'action'; name: Gesture }

/** 打字速度：不管网络一次来多少字，都按这个节奏一个个说出来 */
const CHARS_PER_SECOND = 18
const LONG_PAUSE = 0.32 // 句号、问号、省略号后
const SHORT_PAUSE = 0.12 // 逗号、顿号后
const LONG_STOPS = '。！？!?…'
const SHORT_STOPS = '，、,；;：:'
/** 本地只保留最近这么多条，服务端还会再截断 */
const MAX_HISTORY = 40

/**
 * 一轮轮对话：发消息、收流式回复、把文字按说话节奏放出来，
 * 表情和动作标签排在文字中间，打字打到那里才触发，和那句话同步。
 */
export class Conversation {
  /** holdMs 为 null 表示一直显示（还在说），否则多少毫秒后收起气泡 */
  onBubble: (text: string, holdMs: number | null) => void = () => {}

  private history: ChatMessage[] = []
  private stream: ChatStream | null = null
  private queue: Item[] = []
  private shown = ''
  /**
   * 已经说出口的部分（带标签）。只把这部分存进历史：被打断时，没说出来的话用户并没有听到。
   * 标签也留着，大模型看到自己之前用过标签，后面会继续用。
   */
  private spoken = ''
  private committed = false
  private receiving = false
  private replying = false
  private budget = 0
  private pause = 0

  constructor(
    private behavior: Behavior,
    private transport: ChatTransport
  ) {}

  /** 正在回复（包括等待第一个字和打字中） */
  get busy(): boolean {
    return this.replying
  }

  get messages(): readonly ChatMessage[] {
    return this.history
  }

  send(text: string): void {
    this.interrupt()
    this.history.push({ role: 'user', content: text })
    this.history = this.history.slice(-MAX_HISTORY)

    this.queue = []
    this.shown = ''
    this.spoken = ''
    this.committed = false
    this.budget = 0
    this.pause = 0
    this.receiving = true
    this.replying = true

    this.behavior.attend()
    this.behavior.setThinking(true)
    this.onBubble('……', null)
    // 复制一份普通对象，跨进程传输不能带 Vue 的响应式代理
    const snapshot = this.history.map((m) => ({ role: m.role, content: m.content }))
    this.stream = this.transport.start(snapshot, (ev) => this.onEvent(ev))
  }

  /** 打断当前回复：已经说出口的部分记进历史，免得上下文断掉 */
  interrupt(): void {
    if (!this.replying) return
    this.stream?.cancel()
    this.stream = null
    this.receiving = false
    this.commit()
    this.queue = []
    this.replying = false
    this.behavior.setThinking(false)
    this.behavior.setTalking(false)
  }

  /** 每帧调用 */
  tick(dt: number): void {
    if (!this.replying) return

    if (this.queue.length === 0) {
      this.behavior.setTalking(false)
      if (!this.receiving) this.finish()
      return
    }

    this.behavior.setThinking(false)
    if (this.pause > 0) {
      this.pause -= dt
      this.behavior.setTalking(false)
      return
    }

    this.budget = Math.min(this.budget + dt * CHARS_PER_SECOND, 2)
    let changed = false
    while (this.queue.length > 0 && this.pause <= 0) {
      const item = this.queue[0]
      if (item.kind === 'emotion') {
        this.behavior.emote(item.name)
        this.spoken += `[${item.name}]`
      } else if (item.kind === 'action') {
        this.behavior.gesture(item.name)
        this.spoken += `[${item.name}]`
      } else {
        if (this.budget < 1) break
        this.budget -= 1
        this.shown += item.ch
        this.spoken += item.ch
        changed = true
        if (LONG_STOPS.includes(item.ch)) this.pause = LONG_PAUSE
        else if (SHORT_STOPS.includes(item.ch)) this.pause = SHORT_PAUSE
      }
      this.queue.shift()
    }
    if (changed) this.onBubble(this.shown, null)
    this.behavior.setTalking(this.pause <= 0 && this.queue.length > 0)
  }

  private onEvent(ev: ChatEvent): void {
    if (!this.receiving) return
    switch (ev.type) {
      case 'text':
        for (const ch of ev.text) this.queue.push({ kind: 'char', ch })
        break
      case 'emotion':
        this.queue.push({ kind: 'emotion', name: ev.name })
        break
      case 'action':
        this.queue.push({ kind: 'action', name: ev.name })
        break
      case 'done':
        // 收完了，但可能还没说完：等 tick 把队列放空再结束
        this.receiving = false
        this.stream = null
        break
      case 'error':
        this.receiving = false
        this.stream = null
        this.fail(ev.code, ev.message)
        break
    }
  }

  private commit(): void {
    if (this.committed || !this.spoken) return
    this.committed = true
    this.history.push({ role: 'assistant', content: this.spoken })
  }

  private finish(): void {
    this.commit()
    this.replying = false
    this.behavior.setTalking(false)
    this.behavior.endReply()
    if (this.shown) this.onBubble(this.shown, Math.min(10000, 2500 + this.shown.length * 80))
  }

  private fail(code: ChatErrorCode, detail: string): void {
    console.warn(`[chat] ${code}: ${detail}`)
    this.commit()
    this.queue = []
    this.replying = false
    this.behavior.setThinking(false)
    this.behavior.setTalking(false)
    this.behavior.endReply()
    this.behavior.react('sad')
    this.behavior.say(lines.chatError[code], 6000)
  }
}
