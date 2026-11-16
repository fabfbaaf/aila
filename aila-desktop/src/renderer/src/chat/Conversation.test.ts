import { describe, expect, it } from 'vitest'
import type { ChatEvent, ConfirmAsk } from '../../../shared/chat'
import type { StoredMessage } from '../../../shared/cloud'
import type { Behavior } from '../behavior/Behavior'
import { Conversation } from './Conversation'
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

function setup() {
  const { behavior, calls } = fakeBehavior()
  const transport = new FakeTransport()
  const conv = new Conversation(behavior, transport)
  const asks: (ConfirmAsk | null)[] = []
  const bubbles: string[] = []
  const saved: StoredMessage[] = []
  conv.onAsk = (a) => asks.push(a)
  conv.onBubble = (t) => bubbles.push(t)
  conv.onCommit = (m) => saved.push(m)
  /** 按打字节奏把收到的内容都说完（最多 20 秒） */
  const talk = (): void => {
    for (let i = 0; i < 400; i++) conv.tick(0.05)
  }
  return { conv, transport, calls, asks, bubbles, saved, talk }
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
