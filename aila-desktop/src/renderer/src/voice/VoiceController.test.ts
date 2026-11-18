import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ListenMode, VoiceEvent } from '../../../shared/voice'
import type { Behavior } from '../behavior/Behavior'
import type { Conversation } from '../chat/Conversation'
import { lines } from '../persona/lines'
import type { Speaker } from './Speaker'
import { VoiceController, type Ear, type Mic } from './VoiceController'

/** 假的「耳朵」：记录模式切换，测试里手动推事件 */
class FakeEar implements Ear {
  onEvent: (ev: VoiceEvent) => void = () => {}
  modes: ListenMode[] = []
  opened = false
  open(): void {
    this.opened = true
  }
  close(): void {
    this.opened = false
  }
  setMode(mode: ListenMode): void {
    this.modes.push(mode)
  }
  send(): void {}
  get mode(): ListenMode | undefined {
    return this.modes.at(-1)
  }
}

const fakeMic = (): Mic => ({ onChunk: () => {}, start: async () => {}, stop: () => {}, simulate: async () => {} })

function setup() {
  const ear = new FakeEar()
  const behavior = {
    hearWake: vi.fn(),
    setListening: vi.fn(),
    react: vi.fn(),
    say: vi.fn()
  }
  const conversation = {
    busy: false,
    audible: false,
    recentSpeech: '',
    onReplyEnd: () => {},
    onFirstWords: () => {},
    send: vi.fn(),
    announce: vi.fn(),
    interrupt: vi.fn(),
    answerText: vi.fn((_text: string) => false)
  }
  const speaker = { duck: vi.fn(), lastEndedAt: -Infinity }
  const bubbles: string[] = []
  const vc = new VoiceController(
    behavior as unknown as Behavior,
    conversation as unknown as Conversation,
    speaker as unknown as Speaker,
    ear,
    fakeMic()
  )
  vc.onBubble = (t) => bubbles.push(t)
  return { vc, ear, behavior, conversation, speaker, bubbles }
}

beforeEach(() => vi.useFakeTimers())
afterEach(() => vi.useRealTimers())

describe('VoiceController', () => {
  it('默认开着「说话」：启动时提前加载语音合成；打开说话时也加载，关掉时不加载', async () => {
    const { vc } = setup()
    const warm = vi.fn()
    vc.warm = warm
    await vc.init()
    expect(warm).toHaveBeenCalledWith({ tts: true, ears: false })
    warm.mockClear()
    vc.toggleSpeak() // 关掉
    expect(warm).not.toHaveBeenCalled()
    vc.toggleSpeak() // 打开
    expect(warm).toHaveBeenCalledWith({ tts: true })
  })

  it('唤醒后开始听，用户说完交给对话', () => {
    const { ear, behavior, conversation, bubbles } = setup()
    ear.onEvent({ type: 'wake', keyword: '你好艾拉' })
    expect(behavior.hearWake).toHaveBeenCalled()
    expect(ear.mode).toBe('listen')
    expect(bubbles.at(-1)).toBe(lines.listening)

    ear.onEvent({ type: 'speech_start' })
    ear.onEvent({ type: 'speech_end', seconds: 1.2 })
    ear.onEvent({ type: 'transcript', text: '明天天气怎么样？', emotion: 'neutral', asr_ms: 150 })
    expect(conversation.send).toHaveBeenCalledWith('明天天气怎么样？', true)
  })

  it('她藏起来时（免打扰、别人在全屏）听到唤醒词不应声', () => {
    const { vc, ear, behavior, conversation } = setup()
    vc.muted = true
    ear.onEvent({ type: 'wake', keyword: '你好艾拉' })
    expect(behavior.hearWake).not.toHaveBeenCalled()
    expect(conversation.interrupt).not.toHaveBeenCalled()
    expect(ear.mode).not.toBe('listen')
    vc.muted = false
    ear.onEvent({ type: 'wake', keyword: '你好艾拉' })
    expect(behavior.hearWake).toHaveBeenCalled()
  })

  it('听到的话外面接手了（比如提到剪贴板）就不直接交给对话', () => {
    const { vc, ear, conversation } = setup()
    const heard: string[] = []
    vc.onHeard = (t) => {
      heard.push(t)
      return t.includes('剪贴板')
    }
    ear.onEvent({ type: 'wake', keyword: '你好艾拉' })
    ear.onEvent({ type: 'transcript', text: '看看我剪贴板里的报错', emotion: 'neutral', asr_ms: 100 })
    expect(heard).toEqual(['看看我剪贴板里的报错'])
    expect(conversation.send).not.toHaveBeenCalled()
  })

  it('记录说完到开口的延迟', () => {
    const { vc, ear, conversation } = setup()
    ear.onEvent({ type: 'wake', keyword: '你好艾拉' })
    ear.onEvent({ type: 'transcript', text: '你好', emotion: 'neutral', asr_ms: 150 })
    vi.advanceTimersByTime(600)
    conversation.onFirstWords()
    expect(vc.latencies).toHaveLength(1)
    expect(vc.latencies[0].asr).toBe(150)
    expect(vc.latencies[0].total).toBe(400 + 150 + vc.latencies[0].reply)
  })

  it('只叫了名字：回应一次「我在」，不会重复', () => {
    const { ear, conversation } = setup()
    ear.onEvent({ type: 'wake', keyword: '你好艾拉' })
    ear.onEvent({ type: 'empty', asr_ms: 80 })
    expect(conversation.announce).toHaveBeenCalledTimes(1)
    expect(lines.wakeHere).toContain(conversation.announce.mock.calls[0][0])
    ear.onEvent({ type: 'empty', asr_ms: 80 })
    expect(conversation.announce).toHaveBeenCalledTimes(1)
  })

  it('唤醒后一直没说话，超时回到不听', () => {
    const { ear, bubbles } = setup()
    ear.onEvent({ type: 'wake', keyword: '你好艾拉' })
    vi.advanceTimersByTime(7100)
    expect(ear.mode).toBe('off') // 唤醒没开（只是这次被叫醒），回到关闭
    expect(bubbles.at(-1)).toBe('')
  })

  it('她说完后还能直接追问，过一会儿才回去', () => {
    const { ear, conversation } = setup()
    ear.onEvent({ type: 'wake', keyword: '你好艾拉' })
    ear.onEvent({ type: 'transcript', text: '你好', emotion: 'neutral', asr_ms: 100 })
    conversation.onReplyEnd()
    expect(ear.mode).toBe('listen')
    ear.onEvent({ type: 'transcript', text: '那后天呢？', emotion: 'neutral', asr_ms: 100 })
    expect(conversation.send).toHaveBeenLastCalledWith('那后天呢？', true)
    conversation.onReplyEnd()
    vi.advanceTimersByTime(6100)
    expect(ear.mode).toBe('off')
  })

  it('她说话时用户插话：先压低音量，确认后打断', () => {
    const { ear, conversation, speaker } = setup()
    ear.onEvent({ type: 'wake', keyword: '你好艾拉' })
    ear.onEvent({ type: 'transcript', text: '讲个故事', emotion: 'neutral', asr_ms: 100 })
    conversation.audible = true
    conversation.recentSpeech = '从前有一座山，山里有座庙。'

    ear.onEvent({ type: 'speech_start' })
    expect(speaker.duck).toHaveBeenLastCalledWith(true)
    ear.onEvent({ type: 'transcript', text: '等一下', emotion: 'neutral', asr_ms: 90 })
    expect(speaker.duck).toHaveBeenLastCalledWith(false)
    expect(conversation.send).toHaveBeenLastCalledWith('等一下', true)
  })

  it('她在等确认时说「好」：当作回答，不打断这一轮回复', () => {
    const { ear, conversation } = setup()
    ear.onEvent({ type: 'wake', keyword: '你好艾拉' })
    ear.onEvent({ type: 'transcript', text: '帮我打开 GitHub', emotion: 'neutral', asr_ms: 100 })
    conversation.send.mockClear()
    conversation.busy = true
    conversation.answerText.mockImplementation((text: string) => text === '好的')

    ear.onEvent({ type: 'speech_start' })
    ear.onEvent({ type: 'transcript', text: '好的', emotion: 'neutral', asr_ms: 80 })
    expect(conversation.answerText).toHaveBeenLastCalledWith('好的')
    expect(conversation.send).not.toHaveBeenCalled()

    // 说的不是回答：照常当新的一句话（打断回复，操作不执行）
    ear.onEvent({ type: 'transcript', text: '先帮我查一下天气', emotion: 'neutral', asr_ms: 80 })
    expect(conversation.send).toHaveBeenLastCalledWith('先帮我查一下天气', true)
  })

  it('听到的是她自己的回声：恢复音量，不打断', () => {
    const { ear, conversation, speaker } = setup()
    ear.onEvent({ type: 'wake', keyword: '你好艾拉' })
    ear.onEvent({ type: 'transcript', text: '讲个故事', emotion: 'neutral', asr_ms: 100 })
    conversation.send.mockClear()
    conversation.audible = true
    conversation.recentSpeech = '从前有一座山，山里有座庙。'

    ear.onEvent({ type: 'speech_start' })
    ear.onEvent({ type: 'transcript', text: '山里有座庙', emotion: 'neutral', asr_ms: 90 })
    expect(speaker.duck).toHaveBeenLastCalledWith(false)
    expect(conversation.send).not.toHaveBeenCalled()
  })

  it('插话只是咳嗽：恢复音量继续说', () => {
    const { ear, conversation, speaker } = setup()
    ear.onEvent({ type: 'wake', keyword: '你好艾拉' })
    ear.onEvent({ type: 'transcript', text: '讲个故事', emotion: 'neutral', asr_ms: 100 })
    conversation.audible = true
    ear.onEvent({ type: 'speech_start' })
    ear.onEvent({ type: 'empty', asr_ms: 50 })
    expect(speaker.duck).toHaveBeenLastCalledWith(false)
    expect(conversation.send).toHaveBeenCalledTimes(1)
  })

  it('她刚说完时听到的短回复不当回声（用户说「好的」）', () => {
    const { ear, conversation } = setup()
    ear.onEvent({ type: 'wake', keyword: '你好艾拉' })
    ear.onEvent({ type: 'transcript', text: '帮我整理一下', emotion: 'neutral', asr_ms: 100 })
    conversation.recentSpeech = '好的，马上开始整理。'
    conversation.onReplyEnd()
    vi.advanceTimersByTime(3000) // 她的声音已经停了好一会儿
    ear.onEvent({ type: 'speech_start' })
    ear.onEvent({ type: 'transcript', text: '好的', emotion: 'neutral', asr_ms: 80 })
    expect(conversation.send).toHaveBeenLastCalledWith('好的', true)
  })

  it('没在语音会话时听到的话不处理', () => {
    const { ear, conversation } = setup()
    ear.onEvent({ type: 'transcript', text: '随便说说', emotion: 'neutral', asr_ms: 80 })
    expect(conversation.send).not.toHaveBeenCalled()
  })

  it('语音服务不可用时提示一次', () => {
    const { ear, behavior } = setup()
    ear.onEvent({ type: 'error', message: '缺少语音模型' })
    ear.onEvent({ type: 'error', message: '缺少语音模型' })
    expect(behavior.say).toHaveBeenCalledTimes(1)
  })
})
