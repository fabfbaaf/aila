import type { ListenMode, VoiceEvent } from '../../../shared/voice'

/**
 * 和本地 AI 服务 /voice 的 WebSocket：上行麦克风 PCM，下行唤醒、断句、识别事件。
 * 断线后自动重连（AI 服务重启、晚于桌面端启动都没关系），重连后恢复当前模式。
 */
export class VoiceLink {
  onEvent: (ev: VoiceEvent) => void = () => {}
  onConnection: (connected: boolean) => void = () => {}

  private ws: WebSocket | null = null
  private mode: ListenMode = 'wake'
  private retryMs = 1000
  private retryTimer = 0
  private closed = true

  constructor(private url: string) {}

  get connected(): boolean {
    return this.ws?.readyState === WebSocket.OPEN
  }

  open(): void {
    if (!this.closed) return
    this.closed = false
    this.connect()
  }

  close(): void {
    this.closed = true
    clearTimeout(this.retryTimer)
    this.ws?.close()
    this.ws = null
  }

  setMode(mode: ListenMode): void {
    this.mode = mode
    if (this.connected) this.ws!.send(JSON.stringify({ type: 'mode', mode }))
  }

  send(pcm: ArrayBuffer): void {
    if (this.connected) this.ws!.send(pcm)
  }

  private connect(): void {
    const ws = new WebSocket(this.url)
    ws.binaryType = 'arraybuffer'
    this.ws = ws
    ws.onopen = () => {
      this.retryMs = 1000
      ws.send(JSON.stringify({ type: 'mode', mode: this.mode }))
      this.onConnection(true)
    }
    ws.onmessage = (e) => {
      if (typeof e.data !== 'string') return
      try {
        this.onEvent(JSON.parse(e.data) as VoiceEvent)
      } catch {
        // 不认识的消息直接忽略
      }
    }
    ws.onclose = () => {
      if (this.ws !== ws) return
      this.ws = null
      this.onConnection(false)
      if (this.closed) return
      this.retryTimer = window.setTimeout(() => this.connect(), this.retryMs)
      this.retryMs = Math.min(this.retryMs * 2, 10000)
    }
  }
}
