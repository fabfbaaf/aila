import workletUrl from './pcm-worklet.js?url'

export const MIC_SAMPLE_RATE = 16000
const CHUNK = MIC_SAMPLE_RATE / 10

/**
 * 麦克风：16kHz 单声道 16 位 PCM，每 100ms 回调一次。
 * 打开浏览器自带的回声消除：她自己说话的声音不会被当成用户在说话（语音打断靠这个）。
 */
export class Microphone {
  onChunk: (pcm: ArrayBuffer) => void = () => {}

  private stream: MediaStream | null = null
  private ctx: AudioContext | null = null
  private fake = 0

  get active(): boolean {
    return this.stream !== null || this.fake !== 0
  }

  async start(): Promise<void> {
    if (this.active) return
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 }
    })
    const ctx = new AudioContext({ sampleRate: MIC_SAMPLE_RATE })
    await ctx.audioWorklet.addModule(workletUrl)
    const node = new AudioWorkletNode(ctx, 'pcm16-sender')
    node.port.onmessage = (e: MessageEvent<ArrayBuffer>) => this.onChunk(e.data)
    ctx.createMediaStreamSource(stream).connect(node)
    // 接一个静音输出，保证音频图一直在跑
    const mute = ctx.createGain()
    mute.gain.value = 0
    node.connect(mute).connect(ctx.destination)
    this.stream = stream
    this.ctx = ctx
  }

  stop(): void {
    this.stream?.getTracks().forEach((t) => t.stop())
    void this.ctx?.close()
    this.stream = null
    this.ctx = null
    clearInterval(this.fake)
    this.fake = 0
  }

  /**
   * 调试用：不开麦克风，把一段 16kHz 音频按真实速度喂进去，像有人在说话。
   * 前后补一点静音，方便断句。
   */
  simulate(samples: Float32Array, leadSeconds = 0.5, tailSeconds = 1.5): Promise<void> {
    this.stop()
    const pad = (s: number): Float32Array => new Float32Array(Math.round(s * MIC_SAMPLE_RATE))
    const all = new Float32Array(pad(leadSeconds).length + samples.length + pad(tailSeconds).length)
    all.set(samples, pad(leadSeconds).length)
    let pos = 0
    return new Promise((resolve) => {
      this.fake = window.setInterval(() => {
        if (pos >= all.length) {
          clearInterval(this.fake)
          this.fake = 0
          resolve()
          return
        }
        const chunk = new Int16Array(CHUNK)
        for (let i = 0; i < CHUNK && pos < all.length; i++, pos++) {
          const s = Math.max(-1, Math.min(1, all[pos]))
          chunk[i] = s < 0 ? s * 0x8000 : s * 0x7fff
        }
        this.onChunk(chunk.buffer)
      }, 100)
    })
  }
}
