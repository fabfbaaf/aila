import { contextBridge, ipcRenderer } from 'electron'
import { AI_SERVICE_URL, type ChatEvent, type ChatMessage } from '../shared/chat'
import type {
  AccountStatus,
  AuthForm,
  AuthResult,
  KnowledgeAddResult,
  KnowledgeDoc,
  RemoteChanges,
  SkillInfo,
  StoredMemory,
  StoredMessage
} from '../shared/cloud'
import type {
  AiResult,
  AiSettings,
  AiSettingsUpdate,
  CharacterEvent,
  ScreenStatus,
  SettingsSection,
  VoicePrefs
} from '../shared/settings'
import type { VoiceMenuAction, VoiceMenuState } from '../shared/voice'

// 渲染层能用的全部能力都在这里，渲染层不直接接触 Node 和 Electron
const api = {
  setIgnoreMouse: (ignore: boolean): void => ipcRenderer.send('win:set-ignore-mouse', ignore),
  focusWindow: (): void => ipcRenderer.send('win:focus'),
  blurWindow: (): void => ipcRenderer.send('win:blur'),
  dragStart: (): void => ipcRenderer.send('win:drag-start'),
  dragMove: (): void => ipcRenderer.send('win:drag-move'),
  dragEnd: (): void => ipcRenderer.send('win:drag-end'),
  showMenu: (voice: VoiceMenuState): void => ipcRenderer.send('menu:show', voice),
  getLastModel: (): Promise<ArrayBuffer | null> => ipcRenderer.invoke('model:get-last'),
  onCursor: (cb: (p: { x: number; y: number }) => void): void => {
    ipcRenderer.on('cursor', (_e, p) => cb(p))
  },
  onModelLoaded: (cb: (data: ArrayBuffer) => void): void => {
    ipcRenderer.on('model:loaded', (_e, data) => cb(data))
  },

  // 对话
  chatStart: (id: number, messages: ChatMessage[]): void => ipcRenderer.send('chat:start', id, messages),
  chatCancel: (id: number): void => ipcRenderer.send('chat:cancel', id),
  /** 回答她的确认（「好 / 不要」）；false 表示这个确认已经结束了 */
  chatConfirm: (confirmId: string, approved: boolean): Promise<boolean> =>
    ipcRenderer.invoke('chat:confirm', confirmId, approved),
  onChatEvent: (cb: (id: number, ev: ChatEvent) => void): void => {
    ipcRenderer.on('chat:event', (_e, id, ev) => cb(id, ev))
  },
  /** 右键菜单或全局快捷键要求打开对话框 */
  onChatOpen: (cb: () => void): void => {
    ipcRenderer.on('chat:open', () => cb())
  },

  // 语音
  /** 本地 AI 服务地址（语音 WebSocket 由渲染层直接连） */
  aiUrl: process.env['AILA_AI_URL'] ?? AI_SERVICE_URL,
  synthesize: (text: string): Promise<ArrayBuffer | null> => ipcRenderer.invoke('tts:synthesize', text),
  onVoiceMenu: (cb: (action: VoiceMenuAction) => void): void => {
    ipcRenderer.on('voice:menu', (_e, action) => cb(action))
  },

  // 对话记录（本地保存，登录后同步到云端）
  historyLoad: (): Promise<StoredMessage[]> => ipcRenderer.invoke('history:load'),
  historyAppend: (m: StoredMessage): void => ipcRenderer.send('history:append', m),
  onHistoryRemote: (cb: (changes: RemoteChanges) => void): void => {
    ipcRenderer.on('history:remote', (_e, changes) => cb(changes))
  },
  settingPut: (key: string, value: unknown): void => ipcRenderer.send('settings:put', key, value),

  // 账号
  accountStatus: (): Promise<AccountStatus> => ipcRenderer.invoke('account:status'),
  accountDetails: (): Promise<AccountStatus> => ipcRenderer.invoke('account:details'),
  accountAuth: (form: AuthForm): Promise<AuthResult> => ipcRenderer.invoke('account:auth', form),
  accountLogout: (): Promise<void> => ipcRenderer.invoke('account:logout'),
  onAccountStatus: (cb: (status: AccountStatus) => void): void => {
    ipcRenderer.on('account:status', (_e, status) => cb(status))
  },

  // 记忆
  memoryList: (): Promise<StoredMemory[]> => ipcRenderer.invoke('memory:list'),
  memoryDelete: (id: string): void => ipcRenderer.send('memory:delete', id),
  memoryForgetAll: (): Promise<number> => ipcRenderer.invoke('memory:forget-all'),
  onMemoryChanged: (cb: (list: StoredMemory[]) => void): void => {
    ipcRenderer.on('memory:changed', (_e, list) => cb(list))
  },

  // 资料库（文件只存在本机的 AI 服务里）
  knowledgeList: (): Promise<KnowledgeDoc[]> => ipcRenderer.invoke('knowledge:list'),
  /** 弹出选择文件的对话框并上传；取消时返回 null */
  knowledgeAdd: (): Promise<KnowledgeAddResult | null> => ipcRenderer.invoke('knowledge:add'),
  knowledgeDelete: (id: string): Promise<void> => ipcRenderer.invoke('knowledge:delete', id),
  /** 选好文件、开始上传了（大文件要等一会儿） */
  onKnowledgeBusy: (cb: (count: number) => void): void => {
    ipcRenderer.on('knowledge:busy', (_e, count) => cb(count))
  },

  // 设置窗口
  openSettings: (section?: SettingsSection): void => ipcRenderer.send('settings:open', section),
  onSettingsSection: (cb: (section: SettingsSection) => void): void => {
    ipcRenderer.on('settings:section', (_e, section) => cb(section))
  },
  aiSettings: (): Promise<AiResult<AiSettings>> => ipcRenderer.invoke('ai:settings'),
  aiSettingsPut: (update: AiSettingsUpdate): Promise<AiResult<AiSettings>> => ipcRenderer.invoke('ai:settings:put', update),
  aiSettingsTest: (): Promise<AiResult<{ ok: boolean; message: string }>> => ipcRenderer.invoke('ai:settings:test'),
  /** 技能包列表（带上用户的开关）；AI 服务没开时是 null */
  skillsList: (): Promise<SkillInfo[] | null> => ipcRenderer.invoke('skills:list'),
  skillsSet: (id: string, on: boolean): void => ipcRenderer.send('skills:set', id, on),
  modelPath: (): Promise<string | null> => ipcRenderer.invoke('model:path'),
  modelChoose: (): void => ipcRenderer.send('model:choose'),
  onModelPath: (cb: (path: string) => void): void => {
    ipcRenderer.on('model:path', (_e, path) => cb(path))
  },
  resetPosition: (): void => ipcRenderer.send('win:reset-position'),
  /** 在资源管理器里显示 AI 服务的配置、模型、数据文件夹（只认这几个名字，不接受任意路径） */
  revealPath: (key: 'env' | 'models' | 'data' | 'mcp'): void => ipcRenderer.send('ai:reveal', key),

  // 语音开关：角色窗口报告，设置窗口读写
  voiceReport: (prefs: VoicePrefs): void => ipcRenderer.send('voice:state', prefs),
  voiceGet: (): Promise<VoicePrefs> => ipcRenderer.invoke('voice:get'),
  voiceSet: (key: keyof VoicePrefs, on: boolean): void => ipcRenderer.send('voice:set', key, on),
  /** 看屏幕的状态和上次的统计 */
  screenStatus: (): Promise<AiResult<ScreenStatus>> => ipcRenderer.invoke('screen:status'),
  onVoiceState: (cb: (prefs: VoicePrefs) => void): void => {
    ipcRenderer.on('voice:state', (_e, prefs) => cb(prefs))
  },

  /** 设置窗口里做了某件事，桌面上的她做出反应 */
  onCharacterEvent: (cb: (ev: CharacterEvent) => void): void => {
    ipcRenderer.on('character:event', (_e, ev) => cb(ev))
  }
}

export type AilaApi = typeof api

contextBridge.exposeInMainWorld('aila', api)
