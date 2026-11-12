import type { ListenMode, VoiceEvent, VoiceMenuState } from '../../../shared/voice'
import type { Behavior } from '../behavior/Behavior'
import type { Conversation } from '../chat/Conversation'
import { lines, pick } from '../persona/lines'
import { isEcho } from './echo'
import { Microphone } from './Microphone'
import type { Speaker } from './Speaker'
import type { VoiceLink } from './VoiceLink'

/** 测试时可以换成假的 */
export type Ear = Pick<VoiceLink, 'onEvent' | 'open' | 'close' | 'setMode' | 'send'>
export type Mic = Pick<Microphone, 'onChunk' | 'start' | 'stop' | 'simulate'>
type Timer = ReturnType<typeof setTimeout>

/** 唤醒后多久没开口就回去等唤醒词 */
const AWAIT_MS = 7000
/** 她说完后继续听多久：这段时间里可以直接追问，不用再叫她 */
const FOLLOW_UP_MS = 6000
/** 她的声音停下后这么久内听到的话，要检查是不是回声 */
const ECHO_WINDOW_MS = 1200
/** AI 服务判断「说完了」要等的静音时长（AILA_END_SILENCE 的默认值），算延迟时加上 */
const END_SILENCE_MS = 400
const PREFS_KEY = 'aila.voice'

interface Prefs {
  wake: boolean
  speak: boolean
}

/** 说完到开口的延迟拆分，写进简历的指标就从这里来 */
export interface LatencySample {
  total: number
  asr: number
  reply: number
}

/**
 * 语音会话：
 * - idle：只等唤醒词（没开唤醒就不听）
 * - awaiting：唤醒了 / 她刚说完，等用户开口
 * - conversing：用户说完了，她在想或在说；这时继续听，用户可以插话打断
 */
export class VoiceController {
  onBubble: (text: string, holdMs: number | null) => void = () => {}
  readonly latencies: LatencySample[] = []

  private prefs: Prefs
  private session: 'idle' | 'awaiting' | 'conversing' = 'idle'
  private timer: Timer | undefined
  private bargeIn = false
  private mayBeEcho = false
  private greeted = false
  private pending: { at: number; asr: number } | null = null
  private earsWarned = false

  constructor(
    private behavior: Behavior,
    private conversation: Conversation,
    private speaker: Speaker,
    private link: Ear,
    private mic: Mic = new Microphone()
  ) {
    this.prefs = loadPrefs()
    this.mic.onChunk = (pcm) => this.link.send(pcm)
    this.link.onEvent = (ev) => this.onEvent(ev)
    conversation.onReplyEnd = () => this.onReplyEnd()
    conversation.onFirstWords = () => this.onFirstWords()
  }

  get speakEnabled(): boolean {
    return this.prefs.speak
  }

  menuState(): VoiceMenuState {
    return { ...this.prefs }
  }

  /** 启动时：上次开着唤醒就继续听 */
  async init(): Promise<void> {
    if (this.prefs.wake && !(await this.openMic())) this.setPrefs({ wake: false })
    this.toIdle()
  }

  async toggleWake(): Promise<void> {
    if (this.prefs.wake) {
      this.setPrefs({ wake: false })
      this.toIdle()
      this.conversation.announce(lines.wakeOff)
      return
    }
    if (!(await this.openMic())) return
    this.setPrefs({ wake: true })
    this.toIdle()
    this.conversation.announce(lines.wakeOn)
  }

  toggleSpeak(): void {
    this.setPrefs({ speak: !this.prefs.speak })
    this.conversation.announce(this.prefs.speak ? lines.speakOn : lines.speakOff)
  }

  /** 不用唤醒词，直接开始听（快捷键、菜单「听我说」） */
  async listenNow(): Promise<void> {
    if (!(await this.openMic())) return
    this.conversation.interrupt()
    this.behavior.hearWake()
    this.await(AWAIT_MS, true)
  }

  /** 调试用：把一段 16kHz 音频当成麦克风输入，像用户在说话（按唤醒已开启处理） */
  async simulate(samples: Float32Array): Promise<void> {
    this.mic.stop()
    this.link.open()
    if (this.session === 'idle') this.link.setMode('wake')
    await this.mic.simulate(samples)
  }

  /** 说完到开口的延迟统计（毫秒） */
  latencySummary(): { count: number; p50: number; p95: number } {
    const sorted = this.latencies.map((l) => l.total).sort((a, b) => a - b)
    const at = (q: number): number => sorted[Math.min(sorted.length - 1, Math.floor(q * sorted.length))] ?? 0
    return { count: sorted.length, p50: at(0.5), p95: at(0.95) }
  }

  dispose(): void {
    clearTimeout(this.timer)
    this.mic.stop()
    this.link.close()
  }

  // ---------- 事件 ----------

  private onEvent(ev: VoiceEvent): void {
    switch (ev.type) {
      case 'wake':
        this.conversation.interrupt()
        this.behavior.hearWake()
        this.greeted = false
        this.await(AWAIT_MS, true)
        break

      case 'speech_start':
        clearTimeout(this.timer)
        this.mayBeEcho = this.conversation.audible || performance.now() - this.speaker.lastEndedAt < ECHO_WINDOW_MS
        if (this.conversation.audible) {
          // 她说话时用户开口：先把音量压低，等识别结果确认是在插话再停
          this.speaker.duck(true)
          this.bargeIn = true
        } else if (this.session !== 'idle') {
          this.behavior.setListening(true)
          if (!this.conversation.busy) this.onBubble(lines.listening, null)
        }
        break

      case 'transcript':
        this.onTranscript(ev.text, ev.asr_ms)
        break

      case 'empty':
        this.endBargeIn()
        if (this.session !== 'awaiting') break
        if (!this.greeted && !this.conversation.busy) {
          // 只叫了名字就停下了：回应一声，接着听
          this.greeted = true
          this.conversation.announce(pick(lines.wakeHere))
        } else {
          this.await(AWAIT_MS, false)
        }
        break

      case 'error':
        console.warn('[voice]', ev.message)
        if (!this.earsWarned) {
          this.earsWarned = true
          this.behavior.react('sad')
          this.behavior.say(lines.earsUnavailable, 6000)
        }
        break
    }
  }

  private onTranscript(text: string, asrMs: number): void {
    if (this.session === 'idle') return
    if (this.mayBeEcho && isEcho(text, this.conversation.recentSpeech)) {
      console.info(`[voice] 忽略回声：「${text}」`)
      this.endBargeIn()
      return
    }
    this.endBargeIn()
    clearTimeout(this.timer)
    this.session = 'conversing'
    this.behavior.setListening(false)
    this.pending = { at: performance.now(), asr: asrMs }
    this.conversation.send(text, true) // 她正在说的话会被打断
  }

  private onFirstWords(): void {
    if (!this.pending) return
    const reply = Math.round(performance.now() - this.pending.at)
    const sample = { total: END_SILENCE_MS + this.pending.asr + reply, asr: this.pending.asr, reply }
    this.pending = null
    this.latencies.push(sample)
    console.info(
      `[voice] 说完→开口 ${sample.total}ms（断句等待 ${END_SILENCE_MS} + 识别 ${sample.asr} + 大模型和首句合成 ${sample.reply}）`
    )
  }

  private onReplyEnd(): void {
    if (this.session === 'idle') return // 打字聊天，不是语音会话
    this.await(FOLLOW_UP_MS, false)
  }

  // ---------- 状态 ----------

  /** 等用户开口；超时回到等唤醒词 */
  private await(ms: number, showBubble: boolean): void {
    this.session = 'awaiting'
    this.setMode('listen')
    this.behavior.setListening(true)
    if (showBubble) this.onBubble(lines.listening, null)
    clearTimeout(this.timer)
    this.timer = setTimeout(() => {
      if (this.conversation.busy) this.await(ms, false)
      else {
        if (showBubble) this.onBubble('', 0)
        this.toIdle()
      }
    }, ms)
  }

  private toIdle(): void {
    clearTimeout(this.timer)
    this.session = 'idle'
    this.endBargeIn()
    this.behavior.setListening(false)
    if (this.prefs.wake) {
      this.setMode('wake')
    } else {
      // 「听我说」临时打开的麦克风，用完就关
      this.setMode('off')
      this.mic.stop()
      this.link.close()
    }
  }

  private endBargeIn(): void {
    if (this.bargeIn) this.speaker.duck(false)
    this.bargeIn = false
  }

  private setMode(mode: ListenMode): void {
    this.link.setMode(mode)
  }

  private async openMic(): Promise<boolean> {
    this.link.open()
    try {
      await this.mic.start()
      return true
    } catch (err) {
      console.warn('[voice] 打不开麦克风', err)
      this.behavior.react('sad')
      this.behavior.say(lines.micDenied, 5000)
      return false
    }
  }

  private setPrefs(p: Partial<Prefs>): void {
    this.prefs = { ...this.prefs, ...p }
    try {
      localStorage.setItem(PREFS_KEY, JSON.stringify(this.prefs))
    } catch {
      // 存不了就只在这次运行里生效
    }
  }
}

function loadPrefs(): Prefs {
  // 唤醒默认关闭：一直开着麦克风要用户自己同意；出声默认打开
  const defaults: Prefs = { wake: false, speak: true }
  try {
    return { ...defaults, ...(JSON.parse(localStorage.getItem(PREFS_KEY) ?? '{}') as Partial<Prefs>) }
  } catch {
    return defaults
  }
}
