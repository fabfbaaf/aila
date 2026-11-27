/** 主进程和设置页共用的厂商额度，以及总览如何挑选代表读数。 */
export interface QuotaWindow {
  id: string
  label: string
  remaining: number
  total: number | null
  unit: string
  reset_at: number | null
  minutes: number | null
  source: 'automatic' | 'manual' | 'shared'
  observed_at: number
  stale: boolean
  forecast: { at: number; within_cycle: boolean } | null
}

export interface ProviderQuota {
  id: string
  name: string
  windows: QuotaWindow[]
  status: 'ready' | 'waiting' | 'manual' | 'missing_cli' | 'unavailable'
  linked: boolean
}

export interface QuotaRecord {
  provider?: string
  name?: string
  window?: string
  label: string
  unit: string
  remaining: number
  total: number | null
  reset_at: number | null
}

export interface ClaudeQuotaConfig {
  statusLine: { type: 'command'; command: string }
  powerShell?: string
}

/** 优先展示当前周期。总量都已知时按剩余比例比较。 */
export function summaryWindows(windows: QuotaWindow[], now: number): QuotaWindow[] {
  const active = (w: QuotaWindow): boolean => !w.stale && (w.reset_at === null || w.reset_at > now)
  return [...windows].sort((a, b) => {
    if (active(a) !== active(b)) return active(a) ? -1 : 1
    if ((a.reset_at === null) !== (b.reset_at === null)) return a.reset_at === null ? 1 : -1
    if (a.total !== null && b.total !== null) {
      const difference = a.remaining / a.total - b.remaining / b.total
      if (difference) return difference
    }
    return (a.reset_at ?? Infinity) - (b.reset_at ?? Infinity) || 0
  }).slice(0, 2)
}
