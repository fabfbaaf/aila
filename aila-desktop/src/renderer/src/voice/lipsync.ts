import { closedMouth, type Mouth } from '../avatar/types'

/**
 * 从正在播放的声音估计口型。不追求准确的音素识别，只要嘴型跟着声音的大小和「音色」变化：
 * - 音量决定张多大
 * - 频谱决定是哪种嘴型：中频（700–1400Hz，第一共振峰高）偏「啊」，
 *   高频（1800–3500Hz，第二共振峰高）偏「衣」「诶」，能量集中在低频偏「乌」「哦」
 */

const NOISE_FLOOR = 0.008
const GAIN = 7
const MAX_OPEN = 0.85

export interface Spectrum {
  /** AnalyserNode.getByteFrequencyData 的结果 */
  bins: Uint8Array
  /** 每个频点的宽度（Hz）= sampleRate / fftSize */
  binHz: number
  /** 时域均方根，0–1 */
  rms: number
}

function bandEnergy(s: Spectrum, lo: number, hi: number): number {
  const from = Math.max(1, Math.floor(lo / s.binHz))
  const to = Math.min(s.bins.length - 1, Math.ceil(hi / s.binHz))
  let sum = 0
  for (let i = from; i <= to; i++) sum += s.bins[i] * s.bins[i]
  return sum / Math.max(1, to - from + 1)
}

export function estimateMouth(s: Spectrum): Mouth {
  const open = Math.min(MAX_OPEN, Math.max(0, (s.rms - NOISE_FLOOR) * GAIN))
  if (open === 0) return closedMouth()

  const low = bandEnergy(s, 150, 700)
  const mid = bandEnergy(s, 700, 1400)
  const high = bandEnergy(s, 1800, 3500)
  const total = low + mid + high
  if (total === 0) return { ...closedMouth(), aa: open }
  const l = low / total
  const m = mid / total
  const h = high / total

  const raw: Mouth = {
    aa: m,
    ee: h * m * 2,
    ih: h * (1 - m),
    oh: l * m * 2,
    ou: l * (1 - m) * (1 - h)
  }
  const sum = raw.aa + raw.ee + raw.ih + raw.oh + raw.ou || 1
  // 留一部分给「啊」：只靠频谱判断时嘴型变化太碎，看起来像在抽搐
  const mouth = closedMouth()
  for (const k of Object.keys(raw) as (keyof Mouth)[]) {
    mouth[k] = open * (0.6 * raw[k]) / sum
  }
  mouth.aa += open * 0.4
  return mouth
}
