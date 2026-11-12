import type { ChatErrorCode, ChatEvent, ChatMessage, Emotion, Gesture } from '../../../shared/chat'
import type { Mouth } from '../avatar/types'
import type { Behavior } from '../behavior/Behavior'
import { lines } from '../persona/lines'
import { sentenceCut, speakableLength } from '../voice/sentences'
import type { ChatStream, ChatTransport } from './transport'

type Item =
  | { kind: 'char'; ch: string }
  | { kind: 'emotion'; name: Emotion }
  | { kind: 'action'; name: Gesture }
  | { kind: 'status'; text: string }

/** 让她出声：合成一句、播放一句。没有或关掉时她只打字 */
export interface Voice {
  readonly enabled: boolean
  synthesize(text: string): Promise<AudioBuffer | null>
  /** 返回是否真的出声了（浏览器可能不让） */
  play(buffer: AudioBuffer): boolean
  stop(): void
  readonly playing: boolean
  mouth(): Mouth
}

/** 出声时按句处理：一句合成好、轮到它，才开始播放并显示文字 */
interface Sentence {
  items: Item[]
  text: string
  /** undefined：还在合成；null：没有声音（合成失败或没有能念的字），按打字节奏显示 */
  audio?: AudioBuffer | null
  requested: boolean
}

interface Current {
  items: Item[]
  chars: number
  revealed: number
  t: number
  duration: number
  voiced: boolean
}

/** 进度提示太长会把气泡撑满，截短 */
const MAX_STATUS_CHARS = 24
/** 进度提示至少显示这么久，工具返回很快时也看得清她在干什么 */
const STATUS_MIN_SECONDS = 0.8

/** 打字速度：不管网络一次来多少字，都按这个节奏一个个说出来 */
const CHARS_PER_SECOND = 18
const LONG_PAUSE = 0.32 // 句号、问号、省略号后
const SHORT_PAUSE = 0.12 // 逗号、顿号后
const LONG_STOPS = '。！？!?…'
const SHORT_STOPS = '，、,；;：:'
/** 出声时提前合成几句：播当前句时下一句已经在合成 */
const PREFETCH = 2
/** 本地只保留最近这么多条，服务端还会再截断 */
const MAX_HISTORY = 40

/**
 * 一轮轮对话：发消息、收流式回复、把文字按说话节奏放出来，
 * 表情和动作标签排在文字中间，说到那里才触发，和那句话同步。
 *
 * 两种节奏：
 * - 只打字：每秒 18 个字，标点后停顿
 * - 出声：按句合成语音，一句的文字随着这句声音的长度逐字显示，口型由声音驱动
 */
export class Conversation {
  /** holdMs 为 null 表示一直显示（还在说），否则多少毫秒后收起气泡 */
  onBubble: (text: string, holdMs: number | null) => void = () => {}
  /** 这次回复说完了（正常结束或出错，不包括被打断） */
  onReplyEnd: () => void = () => {}
  /** 开口了：第一句声音开始播放，或第一个字显示出来 */
  onFirstWords: () => void = () => {}

  private history: ChatMessage[] = []
  private stream: ChatStream | null = null
  private shown = ''
  /**
   * 已经说出口的部分（带标签）。只把这部分存进历史：被打断时，没说出来的话用户并没有听到。
   * 标签也留着，大模型看到自己之前用过标签，后面会继续用。
   */
  private spoken = ''
  /** 正在搜索、阅读时显示在气泡最后一行；她开口说下一句时清掉。不算说出口的话，不进历史 */
  private status = ''
  private committed = false
  private receiving = false
  private replying = false
  /** 不是大模型的回复（比如被唤醒时说的「我在」），不进历史 */
  private announcing = false
  private pause = 0
  private spokeYet = false

  // 只打字
  private queue: Item[] = []
  private budget = 0

  // 出声
  private voiced = false
  private sentences: Sentence[] = []
  private building: Item[] = []
  private buildingText = ''
  private sentenceCount = 0
  private current: Current | null = null
  private recent: string[] = []

  constructor(
    private behavior: Behavior,
    private transport: ChatTransport,
    private voice: Voice | null = null
  ) {}

  /** 正在回复（包括等待第一个字和说话中） */
  get busy(): boolean {
    return this.replying
  }

  /** 正在出声 */
  get audible(): boolean {
    return this.voice?.playing ?? false
  }

  /** 最近说的两句（判断听到的是不是自己的回声） */
  get recentSpeech(): string {
    return this.recent.join('')
  }

  get messages(): readonly ChatMessage[] {
    return this.history
  }

  /** heard：这句话是听来的（语音），气泡里先复述一下，让用户知道她听对了没有 */
  send(text: string, heard = false): void {
    this.begin(false)
    this.history.push({ role: 'user', content: text })
    this.history = this.history.slice(-MAX_HISTORY)
    this.receiving = true
    this.behavior.setThinking(true)
    this.onBubble(heard ? `「${text}」\n……` : '……', null)
    // 复制一份普通对象，跨进程传输不能带 Vue 的响应式代理
    const snapshot = this.history.map((m) => ({ role: m.role, content: m.content }))
    this.stream = this.transport.start(snapshot, (ev) => this.onEvent(ev))
  }

  /** 说一句固定台词（不经过大模型，不进历史），出声设置和正常回复一样 */
  announce(text: string): void {
    this.begin(true)
    this.receiving = true
    this.onEvent({ type: 'text', text })
    this.onEvent({ type: 'done' })
  }

  /** 打断当前回复：已经说出口的部分记进历史，免得上下文断掉 */
  interrupt(): void {
    if (!this.replying) return
    this.stream?.cancel()
    this.stream = null
    this.receiving = false
    this.commit()
    this.stopAll()
    this.replying = false
  }

  /** 每帧调用 */
  tick(dt: number): void {
    if (!this.replying) return
    if (this.voiced) this.tickVoiced(dt)
    else this.tickTyped(dt)
  }

  // ---------- 只打字 ----------

  private tickTyped(dt: number): void {
    if (this.queue.length === 0) {
      this.behavior.setTalking(false)
      if (!this.receiving) this.finish()
      return
    }

    if (!this.status) this.behavior.setThinking(false) // 显示进度时保持思考的样子
    if (this.pause > 0) {
      this.pause -= dt
      this.behavior.setTalking(false)
      return
    }

    this.budget = Math.min(this.budget + dt * CHARS_PER_SECOND, 2)
    let changed = false
    while (this.queue.length > 0 && this.pause <= 0) {
      const item = this.queue[0]
      if (item.kind === 'char') {
        if (this.budget < 1) break
        this.budget -= 1
        if (LONG_STOPS.includes(item.ch)) this.pause = LONG_PAUSE
        else if (SHORT_STOPS.includes(item.ch)) this.pause = SHORT_PAUSE
      }
      changed = this.consume(item) || changed
      this.queue.shift()
    }
    if (changed) this.onBubble(this.bubbleText(), null)
    this.behavior.setTalking(this.pause <= 0 && this.queue.length > 0)
  }

  // ---------- 出声 ----------

  private tickVoiced(dt: number): void {
    if (!this.current && !this.startNextSentence(dt)) return
    const c = this.current!
    c.t += dt
    // 文字比声音稍快一点显示完，免得声音说完了字还没出来
    const target = c.chars && Math.min(c.chars, Math.ceil((c.chars * c.t) / Math.max(0.05, c.duration * 0.9)))
    let changed = false
    while (c.items.length > 0 && (c.items[0].kind !== 'char' || c.revealed < target)) {
      const item = c.items.shift()!
      if (item.kind === 'char') c.revealed++
      changed = this.consume(item) || changed
    }
    if (changed) this.onBubble(this.bubbleText(), null)

    if (c.voiced) this.behavior.setMouth(this.voice!.mouth())
    else this.behavior.setTalking(c.items.length > 0)

    // 正常靠播放结束判断；万一没收到结束通知，超过时长 1 秒也算说完，不会卡住
    const soundOver = c.voiced ? !this.voice!.playing || c.t > c.duration + 1 : c.t >= c.duration
    if (c.items.length === 0 && soundOver) {
      this.current = null
      this.behavior.setMouth(null)
      this.behavior.setTalking(false)
    }
  }

  /** 轮到下一句：合成好了就开始播放。返回是否有句子在进行 */
  private startNextSentence(dt: number): boolean {
    if (this.pause > 0) {
      this.pause -= dt
      return false
    }
    const next = this.sentences[0]
    if (!next) {
      this.behavior.setTalking(false)
      if (!this.receiving) this.finish()
      return false
    }
    if (next.audio === undefined) {
      this.request(next) // 还在合成，等着
      return false
    }

    this.sentences.shift()
    const chars = next.items.filter((i) => i.kind === 'char').length
    const voiced = next.audio ? this.voice!.play(next.audio) : false
    if (chars > 0) {
      this.behavior.setThinking(false)
      this.firstWords()
    }
    this.current = {
      items: [...next.items],
      chars,
      revealed: 0,
      t: 0,
      duration: voiced ? next.audio!.duration : chars / CHARS_PER_SECOND + (chars ? LONG_PAUSE : 0),
      voiced
    }
    if (next.text) this.recent = [...this.recent, next.text].slice(-2)
    this.prefetch()
    return true
  }

  /** 收到一个字：攒成句子，句子完整了就去合成 */
  private pushChar(ch: string): void {
    const cut = sentenceCut(this.buildingText, ch, this.sentenceCount === 0)
    if (cut === 'before') this.closeSentence()
    this.building.push({ kind: 'char', ch })
    this.buildingText += ch
    if (cut === 'after') this.closeSentence()
  }

  /** force：没有字（只有标签）也要结束这句；否则标签留给下一句 */
  private closeSentence(force = false): void {
    if (this.building.length === 0 || (!this.buildingText && !force)) return
    const s: Sentence = { items: this.building, text: this.buildingText, requested: false }
    if (speakableLength(s.text) === 0) {
      s.audio = null
      s.requested = true
    }
    this.sentences.push(s)
    this.building = []
    this.buildingText = ''
    this.sentenceCount++
    this.prefetch()
  }

  private prefetch(): void {
    for (const s of this.sentences.slice(0, PREFETCH)) this.request(s)
  }

  private request(s: Sentence): void {
    if (s.requested) return
    s.requested = true
    this.voice!.synthesize(s.text).then(
      (audio) => (s.audio = audio),
      (err) => {
        console.warn('[voice] 合成失败，这句改为只显示文字', err)
        s.audio = null
      }
    )
  }

  // ---------- 共用 ----------

  private begin(announcing: boolean): void {
    this.interrupt()
    this.stopAll()
    this.shown = ''
    this.spoken = ''
    this.status = ''
    this.committed = false
    this.announcing = announcing
    this.pause = 0
    this.budget = 0
    this.spokeYet = false
    this.replying = true
    this.voiced = this.voice?.enabled ?? false
    this.behavior.attend()
  }

  /** 让一个条目生效（字显示出来、标签触发）。返回气泡内容是否变了 */
  private consume(item: Item): boolean {
    switch (item.kind) {
      case 'emotion':
        this.behavior.emote(item.name)
        this.spoken += `[${item.name}]`
        return false
      case 'action':
        this.behavior.gesture(item.name)
        this.spoken += `[${item.name}]`
        return false
      case 'status':
        // 说完「我查一下」才显示进度，然后摆出思考的样子等结果
        this.status = item.text
        this.behavior.setThinking(true)
        this.pause = STATUS_MIN_SECONDS
        return true
      case 'char':
        this.status = ''
        this.shown += item.ch
        this.spoken += item.ch
        if (!this.voiced) this.firstWords()
        return true
    }
  }

  private firstWords(): void {
    if (this.spokeYet) return
    this.spokeYet = true
    this.onFirstWords()
  }

  private bubbleText(): string {
    if (!this.status) return this.shown
    const s = this.status.length > MAX_STATUS_CHARS ? this.status.slice(0, MAX_STATUS_CHARS) + '…' : this.status
    return `${this.shown}${this.shown ? '\n' : ''}（${s}……）`
  }

  private onEvent(ev: ChatEvent): void {
    if (!this.receiving) return
    const item: Item | null =
      ev.type === 'emotion'
        ? { kind: 'emotion', name: ev.name }
        : ev.type === 'action'
          ? { kind: 'action', name: ev.name }
          : ev.type === 'status'
            ? { kind: 'status', text: ev.text }
            : null

    switch (ev.type) {
      case 'text':
        for (const ch of ev.text) {
          if (this.voiced) this.pushChar(ch)
          else this.queue.push({ kind: 'char', ch })
        }
        break
      case 'emotion':
      case 'action':
        if (this.voiced) this.building.push(item!)
        else this.queue.push(item!)
        break
      case 'status':
        if (this.voiced) {
          this.closeSentence(true)
          this.sentences.push({ items: [item!], text: '', audio: null, requested: true })
        } else {
          this.queue.push(item!)
        }
        break
      case 'done':
        // 收完了，但可能还没说完：等 tick 把剩下的说完再结束
        this.receiving = false
        this.stream = null
        if (this.voiced) this.closeSentence(true)
        break
      case 'error':
        this.receiving = false
        this.stream = null
        this.fail(ev.code, ev.message)
        break
    }
  }

  private stopAll(): void {
    this.queue = []
    this.voice?.stop()
    this.sentences = []
    this.building = []
    this.buildingText = ''
    this.sentenceCount = 0
    this.current = null
    this.recent = []
    this.behavior.setThinking(false)
    this.behavior.setTalking(false)
    this.behavior.setMouth(null)
  }

  private commit(): void {
    if (this.committed || !this.spoken || this.announcing) return
    this.committed = true
    this.history.push({ role: 'assistant', content: this.spoken })
  }

  private finish(): void {
    this.commit()
    this.replying = false
    this.behavior.setTalking(false)
    this.behavior.setMouth(null)
    this.behavior.endReply()
    this.status = ''
    // 什么都没说（比如只调了工具就结束）时收起气泡，不让「……」一直挂着
    if (this.shown) this.onBubble(this.shown, Math.min(10000, 2500 + this.shown.length * 80))
    else this.onBubble('', 0)
    this.onReplyEnd()
  }

  private fail(code: ChatErrorCode, detail: string): void {
    console.warn(`[chat] ${code}: ${detail}`)
    this.commit()
    this.stopAll()
    this.replying = false
    this.behavior.endReply()
    this.behavior.react('sad')
    this.behavior.say(lines.chatError[code], 6000)
    this.onReplyEnd()
  }
}
