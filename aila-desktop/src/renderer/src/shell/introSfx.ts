/**
 * 开场演出的音效（shared/intro.ts 的 SfxName）：全用 Web Audio 现合成，仓库里不放音频文件。
 *
 * - 翻页：一声很轻的「刷」（滤过的噪声扫一下）
 * - 打字：短促的「嗒」（高频噪声的一小截，音高每次随机一点）
 * - 故障：电流的「滋滋」（方波加噪声，音量忽大忽小）
 * - 敲屏幕：闷的「咚」（低频正弦往下滑）；裂开：清脆的「咔」（高通噪声一下）
 * - 碎掉：一阵玻璃碎（噪声 + 一串高音「叮」）
 * - 起跳：「呼」（噪声扫上去）；落地：「噗」（低频一下）
 * - 借鼠标时点一下：「嗒」；收尾：两个音的「叮——」
 * - 撞上看不见的玻璃：闷「咚」加一声往上弹的「嘣」；法阵蓄力：越来越高的嗡声加闪闪的沙沙；施法：一串亮的「叮」加一阵风
 *
 * 音量整体压得很低（主音量 0.35），右上角能静音（记在这台电脑上）。
 */
import type { SfxName } from '../../../shared/intro'

const MUTE_KEY = 'aila.intro.muted'

export class IntroSfx {
  private ctx: AudioContext | null = null
  private master: GainNode | null = null
  private noiseBuf: AudioBuffer | null = null
  muted = false

  constructor() {
    try {
      this.muted = localStorage.getItem(MUTE_KEY) === '1'
    } catch {
      // 存不了就每次都有声音
    }
  }

  setMuted(on: boolean): void {
    this.muted = on
    try {
      localStorage.setItem(MUTE_KEY, on ? '1' : '0')
    } catch {
      // 同上
    }
  }

  play(name: SfxName): void {
    if (this.muted) return
    const ctx = this.context()
    if (!ctx) return
    const now = ctx.currentTime
    switch (name) {
      case 'page':
        return this.noise(now, 0.28, { type: 'bandpass', from: 600, to: 3200, q: 0.8 }, 0.22)
      case 'type':
        return this.noise(now, 0.035, { type: 'highpass', from: 2500 + Math.random() * 1500, to: 4000, q: 1 }, 0.16)
      case 'glitch':
        return this.glitch(now, 1.6)
      case 'knock':
        return this.tone(now, 0.18, 'sine', 140, 60, 0.7)
      case 'crack':
        this.noise(now, 0.09, { type: 'highpass', from: 3000, to: 6000, q: 0.7 }, 0.5)
        return this.tone(now, 0.05, 'triangle', 2400, 1800, 0.12)
      case 'shatter':
        this.noise(now, 0.7, { type: 'highpass', from: 1800, to: 900, q: 0.5 }, 0.55)
        for (let i = 0; i < 9; i++) this.tone(now + i * 0.045 + Math.random() * 0.03, 0.22, 'sine', 2600 + Math.random() * 2400, 2400, 0.08)
        return
      case 'whoosh':
        return this.noise(now, 0.5, { type: 'bandpass', from: 400, to: 2600, q: 1.2 }, 0.3)
      case 'thud':
        return this.tone(now, 0.22, 'sine', 110, 45, 0.6)
      case 'click':
        return this.tone(now, 0.04, 'square', 1800, 1200, 0.08)
      case 'bonk':
        this.tone(now, 0.16, 'sine', 160, 70, 0.6)
        return this.tone(now + 0.04, 0.28, 'triangle', 260, 520, 0.16)
      case 'charge':
        this.tone(now, 2.6, 'sawtooth', 110, 880, 0.05)
        this.tone(now, 2.6, 'sine', 220, 1760, 0.08)
        for (let i = 0; i < 12; i++) this.noise(now + i * 0.2, 0.12, { type: 'bandpass', from: 4000 + i * 300, to: 6000, q: 4 }, 0.05 + i * 0.006)
        return
      case 'spell':
        this.noise(now, 0.6, { type: 'bandpass', from: 600, to: 3000, q: 0.8 }, 0.35)
        for (const [k, f] of [1046.5, 1318.5, 1568, 2093].entries()) this.tone(now + k * 0.05, 0.9, 'sine', f, f, 0.12)
        return
      case 'chime':
        this.tone(now, 0.6, 'sine', 880, 880, 0.18)
        return this.tone(now + 0.16, 0.9, 'sine', 1318.5, 1318.5, 0.16)
    }
  }

  private context(): AudioContext | null {
    if (this.ctx) {
      if (this.ctx.state === 'suspended') void this.ctx.resume()
      return this.ctx
    }
    try {
      this.ctx = new AudioContext()
      this.master = this.ctx.createGain()
      this.master.gain.value = 0.35
      this.master.connect(this.ctx.destination)
      // 一秒的白噪声，各种「刷」「咔」「碎」都从这里截
      const buf = this.ctx.createBuffer(1, this.ctx.sampleRate, this.ctx.sampleRate)
      const data = buf.getChannelData(0)
      for (let i = 0; i < data.length; i++) data[i] = Math.random() * 2 - 1
      this.noiseBuf = buf
      return this.ctx
    } catch {
      return null
    }
  }

  /** 一段噪声，经过一个频率从 from 滑到 to 的滤波器，音量快起慢落 */
  private noise(at: number, dur: number, f: { type: BiquadFilterType; from: number; to: number; q: number }, vol: number): void {
    const ctx = this.ctx!
    const src = ctx.createBufferSource()
    src.buffer = this.noiseBuf
    const filter = ctx.createBiquadFilter()
    filter.type = f.type
    filter.Q.value = f.q
    filter.frequency.setValueAtTime(f.from, at)
    filter.frequency.exponentialRampToValueAtTime(Math.max(20, f.to), at + dur)
    const g = this.envelope(at, dur, vol)
    src.connect(filter).connect(g).connect(this.master!)
    src.start(at, Math.random() * 0.4)
    src.stop(at + dur + 0.05)
  }

  /** 一个音，音高从 from 滑到 to */
  private tone(at: number, dur: number, type: OscillatorType, from: number, to: number, vol: number): void {
    const ctx = this.ctx!
    const osc = ctx.createOscillator()
    osc.type = type
    osc.frequency.setValueAtTime(from, at)
    osc.frequency.exponentialRampToValueAtTime(Math.max(20, to), at + dur)
    const g = this.envelope(at, dur, vol)
    osc.connect(g).connect(this.master!)
    osc.start(at)
    osc.stop(at + dur + 0.05)
  }

  /** 电流声：方波的嗡 + 噪声，音量一阵一阵 */
  private glitch(at: number, dur: number): void {
    const ctx = this.ctx!
    const osc = ctx.createOscillator()
    osc.type = 'square'
    osc.frequency.setValueAtTime(70, at)
    const g = ctx.createGain()
    g.gain.setValueAtTime(0, at)
    for (let t = 0; t < dur; t += 0.06) g.gain.setValueAtTime(Math.random() < 0.55 ? 0.05 + Math.random() * 0.08 : 0, at + t)
    g.gain.setValueAtTime(0, at + dur)
    osc.connect(g).connect(this.master!)
    osc.start(at)
    osc.stop(at + dur + 0.05)
    for (let t = 0; t < dur; t += 0.18 + Math.random() * 0.2) this.noise(at + t, 0.06 + Math.random() * 0.08, { type: 'bandpass', from: 1200 + Math.random() * 3000, to: 900, q: 2 }, 0.18)
  }

  private envelope(at: number, dur: number, vol: number): GainNode {
    const g = this.ctx!.createGain()
    g.gain.setValueAtTime(0.0001, at)
    g.gain.exponentialRampToValueAtTime(vol, at + Math.min(0.02, dur / 4))
    g.gain.exponentialRampToValueAtTime(0.0001, at + dur)
    return g
  }
}
