import { describe, expect, it } from 'vitest'
import { estimateMouth, type Spectrum } from './lipsync'

const BIN_HZ = 48000 / 1024

/** 在给定频段放能量的假频谱 */
function spectrum(rms: number, bands: [number, number, number][]): Spectrum {
  const bins = new Uint8Array(512)
  for (const [lo, hi, level] of bands) {
    for (let i = Math.floor(lo / BIN_HZ); i <= Math.ceil(hi / BIN_HZ); i++) bins[i] = level
  }
  return { bins, binHz: BIN_HZ, rms }
}

const total = (m: ReturnType<typeof estimateMouth>): number => m.aa + m.ih + m.ou + m.ee + m.oh

describe('estimateMouth', () => {
  it('安静时闭嘴', () => {
    expect(total(estimateMouth(spectrum(0.002, [[700, 1400, 200]])))).toBe(0)
  })

  it('越大声张得越大，但有上限', () => {
    const quiet = total(estimateMouth(spectrum(0.03, [[700, 1400, 200]])))
    const loud = total(estimateMouth(spectrum(0.1, [[700, 1400, 200]])))
    const veryLoud = total(estimateMouth(spectrum(0.5, [[700, 1400, 200]])))
    expect(loud).toBeGreaterThan(quiet)
    expect(veryLoud).toBeLessThanOrEqual(0.85 + 1e-6)
  })

  it('中频强偏「啊」，高频强偏「衣」，低频强偏「乌」', () => {
    const a = estimateMouth(spectrum(0.1, [[700, 1400, 220]]))
    const i = estimateMouth(spectrum(0.1, [[150, 700, 120], [1800, 3500, 230]]))
    const u = estimateMouth(spectrum(0.1, [[150, 700, 230]]))
    expect(a.aa).toBeGreaterThan(a.ih + a.ou)
    expect(i.ih).toBeGreaterThan(a.ih)
    expect(u.ou).toBeGreaterThan(a.ou)
    expect(u.ou).toBeGreaterThan(u.ih)
  })
})
