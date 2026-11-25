/** 桌面和本地服务之间的关注主题、待分享内容。 */
export const DISCOVERY_SOURCES = {
  gemini: 'https://aistudio.google.com/docs/changelog',
  deepseek: 'https://api-docs.deepseek.com/zh-cn/updates/',
  openai: 'https://developers.openai.com/api/docs/changelog'
} as const

export type DiscoverySource = keyof typeof DISCOVERY_SOURCES
export type DiscoveryAction = 'pause' | 'less'
export interface DiscoveryTopic {
  source: DiscoverySource
  name: string
  state: 'active' | 'muted'
  focus: 'all' | 'releases' | 'pricing' | 'web'
  slow: boolean
}
export interface Discovery {
  id: string
  source: DiscoverySource
  title: string
  summary: string
  created: number
  status: 'pending' | 'shown'
}
export interface DiscoveryState {
  topics: DiscoveryTopic[]
  reports: Discovery[]
}

export function isDiscoverySource(value: unknown): value is DiscoverySource {
  return typeof value === 'string' && Object.hasOwn(DISCOVERY_SOURCES, value)
}

export function isDiscoveryId(value: unknown): value is string {
  return typeof value === 'string' && /^[0-9a-f]{64}$/.test(value)
}
