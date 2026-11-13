// 设置窗口：主进程、预加载和渲染层共用的类型

export type SettingsSection = 'model' | 'voice' | 'skills' | 'knowledge' | 'memory' | 'account' | 'appearance' | 'status'

/** 角色窗口里的开关：语音唤醒、出声说话、留意屏幕上的报错（存在角色窗口那边，设置窗口通过主进程读写） */
export interface VoicePrefs {
  wake: boolean
  speak: boolean
  watch: boolean
}

/** AI 服务看屏幕的状态（GET /screen/status） */
export interface ScreenStatus {
  available: boolean
  watching: boolean
  apps: string[]
  /** 上一次看屏幕的统计：每一层过滤掉多少 */
  stats: {
    ticks: number
    other_app: number
    unchanged: number
    unsettled: number
    ocr: number
    ocr_avg_ms: number
    errors: number
    duplicates: number
    asked: number
  } | null
}

/** 在设置窗口里做了某件事，让她在桌面上有点反应 */
export type CharacterEvent =
  | { kind: 'forget-one' }
  | { kind: 'forget-all' }
  | { kind: 'knowledge-added'; names: string[] }
  | { kind: 'settings-saved' }

/** AI 服务的设置（GET /settings）。密钥只告诉设没设 */
export interface AiSettings {
  llm: { baseUrl: string; model: string; apiKeySet: boolean }
  search: { provider: 'bocha' | 'tavily' | 'none'; apiKeySet: boolean }
  voice: {
    enabled: boolean
    ttsEngine: string
    ttsEngines: { id: string; name: string; installed: boolean }[]
    ttsSpeaker: number
    ttsSpeed: number
    wakeThreshold: number
    endSilence: number
    asrLanguage: 'zh' | 'auto'
  }
  status: Record<string, string>
  mcp: { id: string; name: string; connected: boolean; error: string | null }[]
  paths: { env: string; models: string; data: string; mcp: string }
}

/** 只传要改的项；密钥传空字符串表示清掉 */
export interface AiSettingsUpdate {
  llmBaseUrl?: string
  llmModel?: string
  llmApiKey?: string
  searchProvider?: 'bocha' | 'tavily' | 'none'
  searchApiKey?: string
  ttsEngine?: string
  ttsSpeaker?: number
  ttsSpeed?: number
  wakeThreshold?: number
  endSilence?: number
  asrLanguage?: 'zh' | 'auto'
}

export interface AiResult<T> {
  ok: boolean
  data?: T
  error?: string
}
