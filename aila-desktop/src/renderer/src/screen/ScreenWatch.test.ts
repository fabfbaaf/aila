import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ScreenWatch, helpRequest, type ScreenFinding, type SocketLike } from './ScreenWatch'

class FakeSocket implements SocketLike {
  readyState = 0
  onopen: (() => void) | null = null
  onmessage: ((ev: { data: unknown }) => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  sent: Record<string, unknown>[] = []
  closed = false

  send(data: string): void {
    this.sent.push(JSON.parse(data) as Record<string, unknown>)
  }

  close(): void {
    this.closed = true
    this.readyState = 3
    this.onclose?.()
  }

  // 测试里模拟服务端
  open(): void {
    this.readyState = 1
    this.onopen?.()
  }

  push(msg: Record<string, unknown>): void {
    this.onmessage?.({ data: JSON.stringify(msg) })
  }

  drop(): void {
    this.readyState = 3
    this.onclose?.()
  }
}

const FINDING: ScreenFinding = {
  id: 'e1',
  app: 'VS Code',
  title: 'app.py - demo',
  rule: 'python',
  summary: "TypeError: unsupported operand type(s) for *: 'NoneType' and 'int'",
  snippet: 'Traceback (most recent call last):\n  ...\nTypeError: unsupported operand'
}

function setup() {
  const sockets: FakeSocket[] = []
  const watch = new ScreenWatch('ws://ai/screen', () => {
    const s = new FakeSocket()
    sockets.push(s)
    return s
  })
  const states: { on: boolean; watching: boolean; error?: string }[] = []
  const findings: ScreenFinding[] = []
  watch.onState = (s) => states.push(s)
  watch.onFinding = (f) => findings.push(f)
  return { watch, sockets, states, findings }
}

/** 测试在 Node 里跑，没有 localStorage：放一个内存版的 */
function memoryStorage(): Storage {
  const data = new Map<string, string>()
  return {
    get length() {
      return data.size
    },
    clear: () => data.clear(),
    getItem: (k) => data.get(k) ?? null,
    key: (i) => [...data.keys()][i] ?? null,
    removeItem: (k) => void data.delete(k),
    setItem: (k, v) => void data.set(k, String(v))
  }
}

beforeEach(() => {
  vi.useFakeTimers()
  vi.stubGlobal('localStorage', memoryStorage())
})
afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('ScreenWatch', () => {
  it('默认关着：不连 AI 服务', () => {
    const { watch, sockets } = setup()
    watch.init()
    expect(watch.on).toBe(false)
    expect(sockets).toHaveLength(0)
  })

  it('打开后连上就让 AI 服务开始看，看到报错转给界面', () => {
    const { watch, sockets, states, findings } = setup()
    watch.setEnabled(true)
    const s = sockets[0]
    s.open()
    expect(s.sent).toEqual([{ type: 'start' }])
    s.push({ type: 'started', apps: ['code.exe'] })
    expect(watch.watching).toBe(true)
    expect(states.at(-1)).toEqual({ on: true, watching: true, error: undefined })

    s.push({ type: 'error_seen', ...FINDING })
    expect(findings).toEqual([FINDING])

    watch.declined()
    watch.pause(3600)
    expect(s.sent.slice(1)).toEqual([{ type: 'declined' }, { type: 'pause', seconds: 3600 }])
  })

  it('关掉：告诉 AI 服务停下并断开，记住这个选择', () => {
    const { watch, sockets } = setup()
    watch.setEnabled(true)
    sockets[0].open()
    watch.setEnabled(false)
    expect(sockets[0].sent.at(-1)).toEqual({ type: 'stop' })
    expect(sockets[0].closed).toBe(true)
    expect(watch.watching).toBe(false)
    expect(JSON.parse(localStorage.getItem('aila.screen')!)).toEqual({ watch: false })
  })

  it('AI 服务重启了：过一会儿自动重连', () => {
    const { watch, sockets } = setup()
    watch.setEnabled(true)
    sockets[0].open()
    sockets[0].push({ type: 'started', apps: [] })
    sockets[0].drop()
    expect(watch.watching).toBe(false)
    vi.advanceTimersByTime(2100)
    expect(sockets).toHaveLength(2)
  })

  it('这台电脑看不了：告诉用户原因，不再反复重连', () => {
    const { watch, sockets, states } = setup()
    watch.setEnabled(true)
    sockets[0].open()
    sockets[0].push({ type: 'error', message: '看屏幕现在只支持 Windows' })
    expect(states.at(-1)?.error).toBe('看屏幕现在只支持 Windows')
    vi.advanceTimersByTime(60_000)
    expect(sockets).toHaveLength(1)
  })

  it('组件卸载后（热更新换掉了它），再收到开关也不会打开', () => {
    const { watch, sockets } = setup()
    watch.setEnabled(true)
    sockets[0].open()
    watch.dispose()
    expect(sockets[0].closed).toBe(true)
    watch.setEnabled(false)
    watch.setEnabled(true)
    vi.advanceTimersByTime(60_000)
    expect(sockets).toHaveLength(1)
  })

  it('上次开着，重启后继续看', () => {
    localStorage.setItem('aila.screen', JSON.stringify({ watch: true }))
    const { watch, sockets } = setup()
    watch.init()
    expect(sockets).toHaveLength(1)
  })

  it('发给大模型的话带上报错和出处', () => {
    const text = helpRequest(FINDING)
    expect(text).toContain(FINDING.snippet)
    expect(text).toContain('VS Code「app.py - demo」')
  })
})
