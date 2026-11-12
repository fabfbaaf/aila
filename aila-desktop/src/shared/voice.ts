// 语音相关的类型：主进程、预加载和渲染层共用

/** 右键菜单里语音开关的当前状态 */
export interface VoiceMenuState {
  wake: boolean
  speak: boolean
}
export type VoiceMenuAction = 'toggle-wake' | 'toggle-speak' | 'listen'

// ---------- 本地 AI 服务 /voice 推过来的事件（「耳朵」） ----------

/** off：不听；wake：只等唤醒词；listen：断句 + 识别 */
export type ListenMode = 'off' | 'wake' | 'listen'

export type VoiceEvent =
  | { type: 'ready'; mode: ListenMode }
  | { type: 'mode'; mode: ListenMode }
  | { type: 'wake'; keyword: string }
  | { type: 'speech_start' }
  | { type: 'speech_end'; seconds: number }
  | { type: 'transcript'; text: string; emotion: string; asr_ms: number }
  /** 说完了但没听出内容（咳嗽、语气词、只说了唤醒词） */
  | { type: 'empty'; asr_ms: number }
  | { type: 'error'; message: string }
