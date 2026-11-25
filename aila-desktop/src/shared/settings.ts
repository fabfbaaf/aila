// 设置窗口：主进程、预加载和渲染层共用的类型
import type { AgentProgress } from './agent'
import type { Reminder } from './desktop'

export type SettingsSection =
  | 'model'
  | 'voice'
  | 'skills'
  | 'knowledge'
  | 'code'
  | 'memory'
  | 'account'
  | 'appearance'
  | 'status'
  /** 桌面上的她：全屏时躲开、免打扰、省电、开机启动、提醒 */
  | 'desktop'
  | 'agent'
  /** 扩展包：导入、开关、设置、卸载 */
  | 'extensions'

/** 一个项目里「以后都允许」的命令；路径和命令都按原文删除。 */
export interface AgentRuleGroup {
  project: string
  commands: string[]
}

/** 她修代码的记录：修过几次、跑了测试的几次、测试通过几次、改进用户代码几次（GET /code/fixes） */
export interface FixStats {
  attempts: number
  tested: number
  passed: number
  merged: number
}

/** 登记的代码项目（AI 服务 GET /code/projects） */
export interface CodeProject {
  id: string
  name: string
  root: string
  addedAt: string
  files?: number
  chunks?: number
  languages?: Record<string, number>
  refreshedAt?: number | null
  /** new：还没建索引；indexing：正在建；ready：好了；error：出错了 */
  state: 'new' | 'indexing' | 'ready' | 'error'
  error?: string
  last?: { parsed: number; removed: number; embedded: number; seconds: number }
  /** 「项目」页里加的忽略（文件夹、文件、通配符） */
  ignore?: string[]
  /** 项目里 .ailaignore 写的（只读，改要改那个文件） */
  ignoreFile?: string[]
}

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
  | { kind: 'discovery'; report: import('./discoveries').Discovery }
  | { kind: 'forget-one' }
  | { kind: 'forget-all' }
  | { kind: 'knowledge-added'; names: string[] }
  | { kind: 'settings-saved' }
  /** 工作窗口里的编程任务有进展：她说一句、做个动作 */
  | { kind: 'agent'; progress: AgentProgress }
  /** 提醒到点了（late：程序没开时就过了点，现在补上） */
  | { kind: 'reminder'; reminder: Reminder; late: boolean }
  /** 对话里让她记的提醒没记上（主进程那边检查没过） */
  | { kind: 'reminder-failed'; why: string }
  /** 连续用电脑满 N 分钟了 */
  | { kind: 'sit-break'; minutes: number }
  /** 每天第一次见面说说昨天和今天（morning）、晚上的日报（evening）。detail：有具体的事可说（会给「看看任务」） */
  | { kind: 'briefing'; when: 'morning' | 'evening'; text: string; detail: boolean }
  /** 她自己做好了一件作品：冒个气泡问你要不要看 */
  | { kind: 'work'; work: { id: string; title: string; why: string } }
  /** 她在做自己的作品（on）/ 做完了（off）：在桌面上「敲键盘」 */
  | { kind: 'work-making'; on: boolean; title: string }
  /** 深夜你还在用电脑：提醒一次早点睡 */
  | { kind: 'late-night' }
  /** 这周的回顾写好了（作品页「回顾」） */
  | { kind: 'review'; week: string }

import type { Lang } from './i18n'

/** 她的说话风格：温柔（默认）、元气、冷静、爱吐槽 */
export type PersonaStyle = 'gentle' | 'lively' | 'calm' | 'teasing'

export const PERSONA_STYLES: { id: PersonaStyle; name: string; hint: string }[] = [
  { id: 'gentle', name: '温柔', hint: '安静、认真，偶尔用「……」停顿（原来的她）' },
  { id: 'lively', name: '元气', hint: '更轻快，会说「好耶」「冲」' },
  { id: 'calm', name: '冷静', hint: '话少、直接，像靠谱的资深同事' },
  { id: 'teasing', name: '爱吐槽', hint: '偶尔轻轻吐槽一句，点到为止' }
]

/** AI 服务的设置（GET /settings）。密钥只告诉设没设 */
export interface AiSettings {
  /** coderModel：编程 Agent 用的模型，空 = 和对话一样；coderFastModel：派出去查资料的子 Agent 用的，空 = 和 coderModel 一样 */
  llm: { baseUrl: string; model: string; apiKeySet: boolean; coderModel?: string; coderFastModel?: string }
  search: { provider: 'bocha' | 'tavily' | 'none'; apiKeySet: boolean }
  computer: { inventory: boolean }
  /** 性格：说话风格、怎么称呼你、口头禅（旧服务没有） */
  persona?: { style: PersonaStyle; call: string; catchphrase: string }
  voice: {
    enabled: boolean
    ttsEngine: string
    ttsEngines: { id: string; name: string; installed: boolean }[]
    ttsSpeaker: number
    ttsSpeed: number
    wakeThreshold: number
    endSilence: number
    asrLanguage: 'zh' | 'auto'
    /** 默认要的语音模型里还没下载的类别（kws、vad、asr、tts）；旧版服务没有这一项 */
    modelsMissing?: string[]
  }
  status: Record<string, string>
  mcp: { id: string; name: string; connected: boolean; error: string | null }[]
  paths: { env: string; models: string; data: string; mcp: string }
}

/** 只传要改的项；密钥传空字符串表示清掉 */
export interface AiSettingsUpdate {
  computerInventory?: boolean
  /** 界面语言：en 时她用英文回答 */
  language?: Lang
  personaStyle?: PersonaStyle
  personaCall?: string
  personaCatchphrase?: string
  llmBaseUrl?: string
  llmModel?: string
  llmApiKey?: string
  coderModel?: string
  coderFastModel?: string
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
