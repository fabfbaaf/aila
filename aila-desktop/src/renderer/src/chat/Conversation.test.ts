import { describe, expect, it } from 'vitest'
import type { ChatEvent, ConfirmAsk } from '../../../shared/chat'
import type { StoredMessage } from '../../../shared/cloud'
import type { Behavior } from '../behavior/Behavior'
import { Conversation, trimHistory, type Voice } from './Conversation'
import type { ChatTransport } from './transport'

/** 行为全部是空操作，只记录调用过的方法名 */
function fakeBehavior(): { behavior: Behavior; calls: string[] } {
  const calls: string[] = []
  const behavior = new Proxy(
    {},
    {
      get:
        (_t, name) =>
        (...args: unknown[]) =>
          calls.push(`${String(name)}(${args.map(String).join(',')})`)
    }
  ) as Behavior
  return { behavior, calls }
}

class FakeTransport implements ChatTransport {
  push: (ev: ChatEvent) => void = () => {}
  cancelled = 0
  answers: [string, boolean][] = []
  voiceUrl = ''
  screenUrl = ''

  start(_m: unknown, onEvent: (ev: ChatEvent) => void): { cancel(): void } {
    this.push = onEvent
    return { cancel: () => this.cancelled++ }
  }

  async confirm(id: string, approved: boolean): Promise<boolean> {
    this.answers.push([id, approved])
    return true
  }

  async synthesize(): Promise<ArrayBuffer | null> {
    return null
  }
}

const OPEN: ConfirmAsk = { id: 'c1', skill: '浏览器', action: '在浏览器里打开这个网页', detail: 'https://github.com' }

function setup(voice: Voice | null = null) {
  const { behavior, calls } = fakeBehavior()
  const transport = new FakeTransport()
  const conv = new Conversation(behavior, transport, voice)
  const asks: (ConfirmAsk | null)[] = []
  const bubbles: string[] = []
  const fullTexts: (string | undefined)[] = []
  const saved: StoredMessage[] = []
  conv.onAsk = (a) => asks.push(a)
  conv.onBubble = (t, _hold, fullText) => {
    bubbles.push(t)
    fullTexts.push(fullText)
  }
  conv.onCommit = (m) => saved.push(m)
  /** 按打字节奏把收到的内容都说完（最多 20 秒） */
  const talk = (): void => {
    for (let i = 0; i < 400; i++) conv.tick(0.05)
  }
  return { conv, transport, calls, asks, bubbles, fullTexts, saved, talk }
}

function fakeVoice() {
  const synthesized: string[] = []
  const voice: Voice = {
    enabled: true,
    async synthesize(text) {
      synthesized.push(text)
      return null
    },
    play: () => false,
    stop: () => {},
    playing: false,
    mouth: () => ({ aa: 0, ih: 0, ou: 0, ee: 0, oh: 0 })
  }
  return { voice, synthesized }
}

describe('Conversation 的操作确认', () => {
  it('问完再显示按钮；回答「好」后接着说完', () => {
    const { conv, transport, calls, asks, bubbles, saved, talk } = setup()
    conv.send('帮我打开 https://github.com')
    transport.push({ type: 'text', text: '好，我帮你打开。' })
    transport.push({ type: 'confirm', ...OPEN })
    talk()

    expect(conv.asking).toEqual(OPEN)
    expect(asks).toEqual([OPEN])
    expect(bubbles.at(-1)).toContain('要我在浏览器里打开这个网页吗？')
    expect(calls).toContain('gesture(tilt)')
    expect(conv.busy).toBe(true) // 回复还没结束，在等

    expect(conv.answerText('先帮我查一下天气')).toBe(false) // 不是回答
    expect(conv.answerText('好的')).toBe(true)
    expect(transport.answers).toEqual([['c1', true]])
    expect(conv.asking).toBeNull()
    expect(asks.at(-1)).toBeNull()

    transport.push({ type: 'confirm_end', id: 'c1', result: 'approved' })
    transport.push({ type: 'status', text: '正在打开 github.com' })
    transport.push({ type: 'text', text: '打开了。' })
    transport.push({ type: 'done' })
    talk()

    expect(conv.busy).toBe(false)
    expect(saved.at(-1)?.content).toBe('好，我帮你打开。[tilt]……要我在浏览器里打开这个网页吗？打开了。')
  })

  it('点「不要」：告诉 AI 服务不同意', () => {
    const { conv, transport, talk } = setup()
    conv.send('打开 https://github.com')
    transport.push({ type: 'confirm', ...OPEN })
    talk()
    expect(conv.answer(false)).toBe(true)
    expect(transport.answers).toEqual([['c1', false]])
    expect(conv.answer(true)).toBe(false) // 已经回答过了
  })

  it('打断回复：按钮收起，流被取消（AI 服务那边的确认随之作废）', () => {
    const { conv, transport, asks, talk } = setup()
    conv.send('打开 https://github.com')
    transport.push({ type: 'confirm', ...OPEN })
    talk()
    conv.interrupt()

    expect(transport.cancelled).toBe(1)
    expect(conv.asking).toBeNull()
    expect(asks.at(-1)).toBeNull()
    expect(conv.answer(true)).toBe(false)
    expect(transport.answers).toEqual([])
  })

  it('等太久 AI 服务放弃了：按钮收起，接着说她的回应', () => {
    const { conv, transport, talk } = setup()
    conv.send('打开 https://github.com')
    transport.push({ type: 'confirm', ...OPEN })
    talk()
    transport.push({ type: 'confirm_end', id: 'c1', result: 'timeout' })
    expect(conv.asking).toBeNull()
    transport.push({ type: 'text', text: '那我先不打开了。' })
    transport.push({ type: 'done' })
    talk()
    expect(conv.busy).toBe(false)
  })

  it('还没问到就已经结束的确认，不再显示按钮', () => {
    const { conv, transport, asks, talk } = setup()
    conv.send('打开 https://github.com')
    transport.push({ type: 'text', text: '好的。' })
    transport.push({ type: 'confirm', ...OPEN })
    transport.push({ type: 'confirm_end', id: 'c1', result: 'timeout' })
    transport.push({ type: 'done' })
    talk()
    expect(asks).toEqual([])
  })

  it('没在等确认时，「好」就是普通的一句话', () => {
    const { conv } = setup()
    expect(conv.answerText('好')).toBe(false)
  })
})

describe('Conversation 是不是正在和你对话', () => {
  it('她自己念的固定台词不算（工作窗口的新进展可以盖掉它）；真的在回复你才算', () => {
    const { conv, transport } = setup()
    conv.announce('要改 README.md……可以吗？')
    expect(conv.busy).toBe(true)
    expect(conv.chatting).toBe(false)
    conv.send('你好')
    transport.push({ type: 'text', text: '你好。' })
    expect(conv.chatting).toBe(true)
  })
})

describe('Conversation 的口播和全文', () => {
  it('只把摘要交给合成和气泡，全文和标签顺序保留在历史里', async () => {
    const { voice, synthesized } = fakeVoice()
    const { conv, transport, calls, saved, bubbles, fullTexts, talk } = setup(voice)
    conv.send('帮我查一下价格')
    transport.push({ type: 'emotion', name: 'happy' })
    transport.push({ type: 'text', text: '查到了。', spoken: true })
    transport.push({ type: 'action', name: 'wave' })
    transport.push({ type: 'text', text: '\n1. 京东：320 元', spoken: false })
    transport.push({ type: 'done' })
    await Promise.resolve()
    talk()

    expect(synthesized).toEqual(['查到了。'])
    expect(bubbles.at(-1)).toBe('查到了。')
    expect(fullTexts.at(-1)).toBe('查到了。\n1. 京东：320 元')
    expect(saved.at(-1)?.content).toBe('[happy]查到了。[wave]\n1. 京东：320 元')
    expect(calls).toContain('emote(happy)')
    expect(calls).not.toContain('gesture(wave)')
  })

  it('旧服务的事件没有 spoken，完整文字仍然念出且不增加全文入口', async () => {
    const { voice, synthesized } = fakeVoice()
    const { conv, transport, saved, bubbles, fullTexts, talk } = setup(voice)
    conv.send('你好')
    transport.push({ type: 'text', text: '你好。今天过得怎么样？' })
    transport.push({ type: 'done' })
    await Promise.resolve()
    talk()

    expect(synthesized.join('')).toBe('你好。今天过得怎么样？')
    expect(bubbles.at(-1)).toBe('你好。今天过得怎么样？')
    expect(fullTexts.at(-1)).toBeUndefined()
    expect(saved.at(-1)?.content).toBe('你好。今天过得怎么样？')
  })

  it.each([true, false])('按 hint 提示看全文，提示不进入记录（语音=%s）', async (enabled) => {
    const { voice, synthesized } = fakeVoice()
    const { conv, transport, saved, bubbles, fullTexts, talk } = setup(enabled ? voice : null)
    conv.send('列一下')
    transport.push({ type: 'text', text: '查到了。', spoken: true })
    transport.push({ type: 'text', text: '\n1. 全文细节', spoken: false })
    transport.push({ type: 'text', text: '', spoken: true, hint: 'more' })
    transport.push({ type: 'text', text: '', spoken: true, hint: 'more' })
    transport.push({ type: 'done' })
    await Promise.resolve()
    talk()

    expect(synthesized.map((text) => text.trim())).toEqual(enabled ? ['查到了。', '细节在对话框里。'] : [])
    expect(bubbles.at(-1)).toBe('查到了。\n细节在对话框里。')
    expect(fullTexts.at(-1)).toBe('查到了。\n1. 全文细节')
    expect(saved.at(-1)?.content).toBe('查到了。\n1. 全文细节')
  })

  it.each([true, false])('打断时按服务是否收完保存全文或已显示片段（done=%s）', (done) => {
    const { conv, transport, saved } = setup()
    conv.send('慢慢说')
    transport.push({ type: 'text', text: '会先说一段。', spoken: true })
    transport.push({ type: 'text', text: '\n剩下的是全文。', spoken: false })
    if (done) transport.push({ type: 'done' })
    conv.tick(1 / 18)
    conv.interrupt()
    expect(saved.at(-1)?.content).toBe(done ? '会先说一段。\n剩下的是全文。' : '会')
  })

  it('服务已收完但摘要仍在播放时打断，停止语音并保留全文一次', async () => {
    let playing = false
    const voice: Voice = {
      ...fakeVoice().voice,
      synthesize: async () => ({ duration: 10 }) as AudioBuffer,
      play: () => (playing = true),
      stop: () => { playing = false },
      get playing() { return playing }
    }
    const { conv, transport, saved, bubbles, talk } = setup(voice)
    conv.send('慢慢说')
    transport.push({ type: 'text', text: '会先说一段。', spoken: true })
    transport.push({ type: 'text', text: '\n剩下的是全文。', spoken: false })
    transport.push({ type: 'done' })
    await Promise.resolve()
    conv.tick(0.1)
    expect(conv.audible).toBe(true)
    expect(bubbles.at(-1)).toBe('会')

    conv.interrupt()
    talk()
    expect(conv.audible).toBe(false)
    expect(saved.filter((message) => message.role === 'assistant').map((message) => message.content))
      .toEqual(['会先说一段。\n剩下的是全文。'])
  })

  it('新回复不沿用上一轮的 done，中途打断仍只记录已显示片段', () => {
    const { conv, transport, saved } = setup()
    conv.send('第一轮')
    transport.push({ type: 'text', text: '第一轮全文。', spoken: false })
    transport.push({ type: 'done' })
    conv.send('第二轮')
    transport.push({ type: 'text', text: '会先说一段。', spoken: true })
    transport.push({ type: 'text', text: '\n还没收完的详情。', spoken: false })
    conv.tick(1 / 18)
    conv.interrupt()

    expect(saved.filter((message) => message.role === 'assistant').map((message) => message.content))
      .toEqual(['第一轮全文。', '会'])
  })
})

describe('trimHistory', () => {
  it('超过上限时一次丢一批，开头隔好几条才变', () => {
    const list = Array.from({ length: 40 }, (_, i) => i)
    expect(trimHistory(list)).toBe(list)
    const over = trimHistory([...list, 40])
    expect(over).toHaveLength(30)
    expect(over[0]).toBe(11)
    // 接下来 10 条里开头都不变
    let h = over
    for (let i = 41; i < 51; i++) h = trimHistory([...h, i])
    expect(h[0]).toBe(11)
    expect(trimHistory([...h, 51])[0]).toBe(22)
  })
})
