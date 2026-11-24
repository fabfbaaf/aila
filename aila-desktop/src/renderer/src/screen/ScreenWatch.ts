import { t } from '../i18n'
/**
 * 「留意屏幕上的报错」：连本地 AI 服务的 /screen，开着时 AI 服务每 2 秒看一眼前台窗口
 * （只看写代码的程序），认出报错就推过来。截图和识别都在 AI 服务那边、只在内存里。
 *
 * 只有这条连接开着并发了 start，AI 服务才看；关掉开关、桌面端退出，连接一断就停。
 * AI 服务重启了会自动重连。
 */

export interface ScreenFinding {
  id: string
  /** 哪个程序，比如「VS Code」「终端」 */
  app: string
  title: string
  rule: string
  /** 一行，比如「TypeError: unsupported operand…」 */
  summary: string
  /** 报错附近的几行（已经把密钥这类打了码），用户同意后才发给大模型 */
  snippet: string
}

type ScreenMessage =
  | { type: 'started'; apps: string[] }
  | { type: 'stopped' }
  | ({ type: 'error_seen' } & ScreenFinding)
  | { type: 'error'; message: string }
  | { type: 'stats'; stats: unknown }

/** 测试时换成假的 */
export interface SocketLike {
  readonly readyState: number
  onopen: (() => void) | null
  onmessage: ((ev: { data: unknown }) => void) | null
  onclose: (() => void) | null
  onerror: (() => void) | null
  send(data: string): void
  close(): void
}

const PREFS_KEY = 'aila.screen'
const OPEN = 1
const RETRY_MS = [2000, 5000, 15000, 30000]

export class ScreenWatch {
  /** 认出了一个报错 */
  onFinding: (f: ScreenFinding) => void = () => {}
  /** 开关或连接状态变了；error 是看不了的原因（不是 Windows、缺组件） */
  onState: (s: { on: boolean; watching: boolean; error?: string }) => void = () => {}

  private enabled: boolean
  private socket: SocketLike | null = null
  private started = false
  private failed = false
  private disposed = false
  private retries = 0
  private timer: ReturnType<typeof setTimeout> | undefined

  constructor(
    private url: string,
    private connect: (url: string) => SocketLike = (u) => new WebSocket(u) as unknown as SocketLike
  ) {
    this.enabled = loadEnabled()
  }

  /** 开关（存在本机：要不要看屏幕每台电脑单独决定，不跟账号同步） */
  get on(): boolean {
    return this.enabled
  }

  /** 正在看（连上了，AI 服务开始看了） */
  get watching(): boolean {
    return this.started
  }

  init(): void {
    if (this.enabled) this.open()
  }

  setEnabled(on: boolean): void {
    if (on === this.enabled) return
    this.enabled = on
    this.failed = false
    saveEnabled(on)
    if (on) this.open()
    else this.shutdown()
    this.emitState()
  }

  /** 「一小时内别提醒」 */
  pause(seconds: number): void {
    this.send({ type: 'pause', seconds })
  }

  /** 用户说「不用」：安静一会儿 */
  declined(): void {
    this.send({ type: 'declined' })
  }

  /** 不再用这个实例了（组件卸载）：断开，并且以后也不会再被打开 */
  dispose(): void {
    clearTimeout(this.timer)
    this.disposed = true
    this.shutdown()
  }

  private open(): void {
    clearTimeout(this.timer)
    if (this.socket || this.disposed) return
    let socket: SocketLike
    try {
      socket = this.connect(this.url)
    } catch {
      this.retry()
      return
    }
    this.socket = socket
    socket.onopen = () => socket.send(JSON.stringify({ type: 'start' }))
    socket.onmessage = (ev) => this.onMessage(ev.data)
    socket.onerror = () => {}
    socket.onclose = () => {
      if (this.socket !== socket) return
      this.socket = null
      const was = this.started
      this.started = false
      if (was) this.emitState()
      if (this.enabled && !this.failed) this.retry()
    }
  }

  private onMessage(data: unknown): void {
    let msg: ScreenMessage
    try {
      msg = JSON.parse(String(data)) as ScreenMessage
    } catch {
      return
    }
    switch (msg.type) {
      case 'started':
        this.started = true
        this.retries = 0
        this.emitState()
        break
      case 'error_seen':
        this.onFinding({
          id: msg.id,
          app: msg.app,
          title: msg.title,
          rule: msg.rule,
          summary: msg.summary,
          snippet: msg.snippet
        })
        break
      case 'error':
        // 这台电脑看不了（不是 Windows、缺组件）：不再重连，等用户重新打开开关
        this.failed = true
        this.started = false
        this.emitState(msg.message)
        this.socket?.close()
        break
    }
  }

  private retry(): void {
    clearTimeout(this.timer)
    const wait = RETRY_MS[Math.min(this.retries, RETRY_MS.length - 1)]
    this.retries++
    this.timer = setTimeout(() => {
      if (this.enabled && !this.failed) this.open()
    }, wait)
  }

  private shutdown(): void {
    clearTimeout(this.timer)
    const socket = this.socket
    this.socket = null
    this.started = false
    if (socket) {
      if (socket.readyState === OPEN) socket.send(JSON.stringify({ type: 'stop' }))
      socket.close()
    }
  }

  private send(msg: Record<string, unknown>): void {
    if (this.socket?.readyState === OPEN) this.socket.send(JSON.stringify(msg))
  }

  private emitState(error?: string): void {
    this.onState({ on: this.enabled, watching: this.started, error })
  }
}

function loadEnabled(): boolean {
  try {
    return (JSON.parse(localStorage.getItem(PREFS_KEY) ?? '{}') as { watch?: boolean }).watch === true
  } catch {
    return false // 默认不看：要用户自己打开
  }
}

function saveEnabled(on: boolean): void {
  try {
    localStorage.setItem(PREFS_KEY, JSON.stringify({ watch: on }))
  } catch {
    // 存不了就只在这次运行里生效
  }
}

/** 用户同意后发给大模型的那句话 */
export function helpRequest(f: ScreenFinding): string {
  const where = f.title ? `${f.app}「${f.title}」` : f.app
  return t('我这边报错了，帮我看看是怎么回事、怎么改：') + `\n${f.snippet}\n` + t('（在 {where} 里看到的）', { where })
}
