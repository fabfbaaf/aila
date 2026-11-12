import { closedMouth, type Mouth } from '../avatar/types'
import { estimateMouth } from './lipsync'

/**
 * 播放她的声音。声音经过：音量（用户插话时先压低）→ 频谱分析（驱动口型）→ 扬声器。
 */
export class Speaker {
  private ctx: AudioContext | null = null
  private gain: GainNode | null = null
  private analyser: AnalyserNode | null = null
  private source: AudioBufferSourceNode | null = null
  private bins = new Uint8Array(0)
  private wave = new Float32Array(0)
  /** 上一句声音结束的时间（performance.now），判断回声用 */
  lastEndedAt = -Infinity

  get playing(): boolean {
    return this.source !== null
  }

  /** WAV → 可以播放的音频 */
  async decode(wav: ArrayBuffer): Promise<AudioBuffer> {
    return this.context().decodeAudioData(wav)
  }

  /** 返回是否真的开始播放了 */
  play(buffer: AudioBuffer, onEnd: () => void = () => {}): boolean {
    this.stop()
    const ctx = this.context()
    if (ctx.state !== 'running') {
      // 浏览器要求用户操作过页面才能出声（桌面版没有这个限制）。这句先不出声，下一句再试
      void ctx.resume()
      return false
    }
    const source = ctx.createBufferSource()
    source.buffer = buffer
    source.connect(this.gain!)
    source.onended = () => {
      if (this.source !== source) return
      this.source = null
      this.lastEndedAt = performance.now()
      onEnd()
    }
    this.gain!.gain.value = 1
    source.start()
    this.source = source
    return true
  }

  stop(): void {
    const s = this.source
    if (s) this.lastEndedAt = performance.now()
    this.source = null
    try {
      s?.stop()
    } catch {
      // 还没开始或已经结束
    }
  }

  /** 用户开始插话：先把音量压低，确认真的在说话再停 */
  duck(on: boolean): void {
    if (!this.ctx || !this.gain) return
    this.gain.gain.setTargetAtTime(on ? 0.25 : 1, this.ctx.currentTime, 0.03)
  }

  /** 当前这一帧的口型 */
  mouth(): Mouth {
    if (!this.analyser || !this.source) return closedMouth()
    this.analyser.getByteFrequencyData(this.bins)
    this.analyser.getFloatTimeDomainData(this.wave)
    let sum = 0
    for (const v of this.wave) sum += v * v
    return estimateMouth({
      bins: this.bins,
      binHz: this.ctx!.sampleRate / this.analyser.fftSize,
      rms: Math.sqrt(sum / this.wave.length)
    })
  }

  private context(): AudioContext {
    if (!this.ctx) {
      const ctx = new AudioContext()
      this.gain = ctx.createGain()
      this.analyser = ctx.createAnalyser()
      this.analyser.fftSize = 1024
      this.analyser.smoothingTimeConstant = 0.5
      this.gain.connect(this.analyser).connect(ctx.destination)
      this.bins = new Uint8Array(this.analyser.frequencyBinCount)
      this.wave = new Float32Array(this.analyser.fftSize)
      this.ctx = ctx
    }
    return this.ctx
  }
}
