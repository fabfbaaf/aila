/** 桌面和本地服务之间的关注主题、待分享内容。 */
export const DISCOVERY_SOURCES = {
  gemini: 'https://aistudio.google.com/docs/changelog',
  deepseek: 'https://api-docs.deepseek.com/zh-cn/updates/',
  openai: 'https://developers.openai.com/api/docs/changelog'
} as const

export type DiscoverySource = keyof typeof DISCOVERY_SOURCES
export type DiscoveryAction = 'pause' | 'less'
export const DISCOVERY_NAMES: Record<DiscoverySource, string> = {
  gemini: 'Gemini', deepseek: 'DeepSeek', openai: 'OpenAI'
}
export const DISCOVERY_FOCUSES = ['releases', 'pricing', 'web'] as const
export const DISCOVERY_DELIVERIES = ['daily', 'weekly', 'history'] as const
export type DiscoveryFocus = 'all' | (typeof DISCOVERY_FOCUSES)[number]
export type DiscoveryDelivery = (typeof DISCOVERY_DELIVERIES)[number]
export type DiscoveryOrigin = 'chat' | 'learned' | 'manual' | 'legacy'

export interface DiscoveryRule {
  source: DiscoverySource
  focuses: DiscoveryFocus[]
  delivery: DiscoveryDelivery
  preference: string
}

export interface DiscoveryTopic extends DiscoveryRule {
  name: string
  state: 'active' | 'muted'
  origin: DiscoveryOrigin
}
export interface Discovery {
  id: string
  source: DiscoverySource
  title: string
  summary: string
  created: number
  status: 'pending' | 'shown' | 'history'
  shown?: number | null
  excerpt?: string
  rule?: Pick<DiscoveryRule, 'focuses' | 'preference'> & { origin: DiscoveryOrigin }
}
export interface DiscoveryState {
  topics: DiscoveryTopic[]
  reports: Discovery[]
}

export interface DiscoveryHistory {
  reports: Discovery[]
  next_before: string | null
}

export function cleanDiscoveryRule(raw: unknown): DiscoveryRule | null {
  if (!raw || typeof raw !== 'object') return null
  const { source, focuses, delivery, preference } = raw as Partial<DiscoveryRule>
  const mode = DISCOVERY_DELIVERIES.find((allowed) => allowed === delivery)
  if (!isDiscoverySource(source) || !Array.isArray(focuses) || focuses.length < 1 || focuses.length > 3
      || !focuses.every((focus) => focus === 'all' || DISCOVERY_FOCUSES.some((allowed) => allowed === focus))
      || !mode
      || typeof preference !== 'string' || preference.length > 180) return null
  return { source, focuses: [...new Set(focuses)], delivery: mode, preference: preference.trim() }
}

export function isDiscoverySource(value: unknown): value is DiscoverySource {
  return typeof value === 'string' && Object.hasOwn(DISCOVERY_SOURCES, value)
}

export function isDiscoveryId(value: unknown): value is string {
  return typeof value === 'string' && /^[0-9a-f]{64}$/.test(value)
}
