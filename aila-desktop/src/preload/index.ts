import { contextBridge, ipcRenderer, webUtils, type IpcRendererEvent } from 'electron'
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
  AgentRuleGroup,
  AiResult,
  AiSettings,
  AiSettingsUpdate,
  CharacterEvent,
  CodeProject,
  FixStats,
  ScreenStatus,
  SettingsSection,
  VoicePrefs
} from '../shared/settings'
import type { VoiceMenuAction, VoiceMenuState } from '../shared/voice'
import type { McpPreviewItem, McpServerInfo } from '../shared/mcp'
import type { DiaryEntry, Work, WorkDetail } from '../shared/studio'
import type { AgentActivity, AgentBudgetEvent, AgentCommandList, AgentEvent, AgentKind, AgentLedger, AgentMemoryNote, AgentMode, AgentSession, AgentSessionSummary, AgentUndoEvent, ComputerContext } from '../shared/agent'
import type { ChunkSource, CodeGraph, LocatedError, OutlineItem, PendingFix, WorkbenchFocus } from '../shared/workbench'
import { cleanRoute, type ShellRoute } from '../shared/shell'
import type { DesktopPrefs, DesktopStatus } from '../shared/desktop'
import type { DropInfo } from '../shared/drop'
import type { ServiceLog, ServiceResult, ServiceState } from '../shared/service'
import type { ModelDownloadState } from '../shared/models'
import type { ExtensionList, ExtensionPlan, ExtensionUpdate } from '../shared/extensions'

/** 取消订阅 */
export type Unsubscribe = () => void

/**
 * 订阅主进程推过来的消息，返回取消订阅的函数。
 * 开发时热更新会换掉页面组件：旧组件要退订，否则它还在响应菜单（比如偷偷把「看屏幕」又打开）。
 */
function listen<A extends unknown[]>(channel: string, cb: (...args: A) => void): Unsubscribe {
  const handler = (_e: IpcRendererEvent, ...args: unknown[]): void => cb(...(args as A))
  ipcRenderer.on(channel, handler)
  return () => void ipcRenderer.removeListener(channel, handler)
}

// 渲染层能用的全部能力都在这里，渲染层不直接接触 Node 和 Electron
const api = {
  // AI 服务托管：日志和重启在主进程核对主窗口身份。
  serviceStatus: (): Promise<ServiceState> => ipcRenderer.invoke('service:status'),
  serviceLogs: (): Promise<AiResult<ServiceLog[]>> => ipcRenderer.invoke('service:logs'),
  serviceRestart: (): Promise<ServiceResult> => ipcRenderer.invoke('service:restart'),
  onServiceState: (cb: (state: ServiceState) => void): Unsubscribe => listen('service:state', cb),
  // 下载语音模型（只有主窗口能用）
  modelsStatus: (): Promise<ModelDownloadState | null> => ipcRenderer.invoke('models:status'),
  modelsDownload: (): Promise<{ ok: boolean; error?: string }> => ipcRenderer.invoke('models:download'),
  onModelsState: (cb: (state: ModelDownloadState) => void): Unsubscribe => listen('models:state', cb),
  setIgnoreMouse: (ignore: boolean): void => ipcRenderer.send('win:set-ignore-mouse', ignore),
  focusWindow: (): void => ipcRenderer.send('win:focus'),
  blurWindow: (): void => ipcRenderer.send('win:blur'),
  dragStart: (): void => ipcRenderer.send('win:drag-start'),
  dragMove: (): void => ipcRenderer.send('win:drag-move'),
  dragEnd: (): void => ipcRenderer.send('win:drag-end'),
  showMenu: (voice: VoiceMenuState): void => ipcRenderer.send('menu:show', voice),
  getLastModel: (): Promise<ArrayBuffer | null> => ipcRenderer.invoke('model:get-last'),
  onCursor: (cb: (p: { x: number; y: number }) => void): Unsubscribe => listen('cursor', cb),
  onModelLoaded: (cb: (data: ArrayBuffer) => void): Unsubscribe => listen('model:loaded', cb),

  // 对话
  chatStart: (id: number, messages: ChatMessage[]): void => ipcRenderer.send('chat:start', id, messages),
  chatCancel: (id: number): void => ipcRenderer.send('chat:cancel', id),
  /** 回答她的确认（「好 / 不要」）；false 表示这个确认已经结束了 */
  chatConfirm: (confirmId: string, approved: boolean): Promise<boolean> =>
    ipcRenderer.invoke('chat:confirm', confirmId, approved),
  onChatEvent: (cb: (id: number, ev: ChatEvent) => void): Unsubscribe => listen('chat:event', cb),
  /** 右键菜单或全局快捷键要求打开对话框 */
  onChatOpen: (cb: () => void): Unsubscribe => listen('chat:open', cb),

  // 语音
  /** 本地 AI 服务地址（语音 WebSocket 由渲染层直接连） */
  aiUrl: process.env['AILA_AI_URL'] ?? AI_SERVICE_URL,
  synthesize: (text: string): Promise<ArrayBuffer | null> => ipcRenderer.invoke('tts:synthesize', text),
  voiceWarmup: (parts: { tts?: boolean; ears?: boolean }): Promise<void> => ipcRenderer.invoke('voice:warmup', parts),
  onVoiceMenu: (cb: (action: VoiceMenuAction) => void): Unsubscribe => listen('voice:menu', cb),

  // 对话记录（本地保存，登录后同步到云端）
  historyLoad: (): Promise<StoredMessage[]> => ipcRenderer.invoke('history:load'),
  historyAppend: (m: StoredMessage): void => ipcRenderer.send('history:append', m),
  onHistoryRemote: (cb: (changes: RemoteChanges) => void): Unsubscribe => listen('history:remote', cb),
  /** 主窗口的对话记录页：记录变了（说了新的话、别的设备同步过来）就重新读 */
  onHistoryChanged: (cb: () => void): Unsubscribe => listen('history:changed', cb),
  /** 从主窗口叫她：在桌面上打开对话框 */
  openChat: (): void => ipcRenderer.send('chat:open-request'),
  settingPut: (key: string, value: unknown): void => ipcRenderer.send('settings:put', key, value),

  // 账号
  accountStatus: (): Promise<AccountStatus> => ipcRenderer.invoke('account:status'),
  accountDetails: (): Promise<AccountStatus> => ipcRenderer.invoke('account:details'),
  accountAuth: (form: AuthForm): Promise<AuthResult> => ipcRenderer.invoke('account:auth', form),
  accountLogout: (): Promise<void> => ipcRenderer.invoke('account:logout'),
  onAccountStatus: (cb: (status: AccountStatus) => void): Unsubscribe => listen('account:status', cb),

  // 记忆
  memoryList: (): Promise<StoredMemory[]> => ipcRenderer.invoke('memory:list'),
  memoryDelete: (id: string): void => ipcRenderer.send('memory:delete', id),
  memoryForgetAll: (): Promise<number> => ipcRenderer.invoke('memory:forget-all'),
  onMemoryChanged: (cb: (list: StoredMemory[]) => void): Unsubscribe => listen('memory:changed', cb),

  // 资料库（文件只存在本机的 AI 服务里）
  knowledgeList: (): Promise<KnowledgeDoc[]> => ipcRenderer.invoke('knowledge:list'),
  /** 弹出选择文件的对话框并上传；取消时返回 null */
  knowledgeAdd: (): Promise<KnowledgeAddResult | null> => ipcRenderer.invoke('knowledge:add'),
  knowledgeDelete: (id: string): Promise<void> => ipcRenderer.invoke('knowledge:delete', id),
  /** 选好文件、开始上传了（大文件要等一会儿） */
  // 代码星图
  codeGraph: (id: string): Promise<AiResult<CodeGraph>> => ipcRenderer.invoke('code:graph', id),
  codeOutline: (id: string, file: string): Promise<AiResult<OutlineItem[]>> => ipcRenderer.invoke('code:outline', id, file),
  codeChunk: (id: string, file: string, line: number): Promise<AiResult<ChunkSource>> =>
    ipcRenderer.invoke('code:chunk', id, file, line),
  /** 报错落在哪个登记的项目里（data 为 null：不在任何项目里） */
  codeLocateAll: (text: string): Promise<AiResult<LocatedError | null>> => ipcRenderer.invoke('code:locate-all', text),
  openWorkbench: (focus?: WorkbenchFocus): void => ipcRenderer.send('workbench:open', focus),
  /** 星图页面加载好后取一次：打开它时要定位的报错 */
  workbenchTakeFocus: (): Promise<WorkbenchFocus | null> => ipcRenderer.invoke('workbench:take-focus'),
  onWorkbenchFocus: (cb: (focus: WorkbenchFocus) => void): Unsubscribe => listen('workbench:focus', cb),
  /** 星图里点「问她」：让桌面上的她说（主进程只认星图窗口发来的） */
  sayToAila: (text: string): void => ipcRenderer.send('chat:say', text),
  onChatSay: (cb: (text: string) => void): Unsubscribe => listen('chat:say', cb),
  /** 角色窗口：报告现在有没有等用户点头的代码修改 */
  reportPendingFix: (fix: PendingFix | null): void => ipcRenderer.send('fix:pending', fix),
  /** 星图窗口：等用户点头的代码修改（打开时取一次，之后听变化） */
  pendingFix: (): Promise<PendingFix | null> => ipcRenderer.invoke('workbench:pending-fix'),
  onPendingFix: (cb: (fix: PendingFix | null) => void): Unsubscribe => listen('workbench:pending-fix', cb),
  /** 星图窗口：双手合十，确认正显示着的这份修改；false 表示它已经不在等了 */
  gestureSeal: (id: string): Promise<boolean> => ipcRenderer.invoke('gesture:seal', id),
  /** 角色窗口：星图里结印确认了这份修改 */
  onGestureApprove: (cb: (id: string) => void): Unsubscribe => listen('gesture:approve', cb),

  // 工作窗口（编程 Agent）：主进程只认工作窗口发来的
  agentList: (q = ''): Promise<AiResult<AgentSessionSummary[]>> => ipcRenderer.invoke('agent:list', q),
  agentGet: (sid: string): Promise<AiResult<AgentSession>> => ipcRenderer.invoke('agent:get', sid),
  agentCreate: (cwd: string | undefined, mode: AgentMode, isolate = false, budget: number | null = null, kind: AgentKind = 'project'): Promise<AiResult<AgentSessionSummary>> =>
    ipcRenderer.invoke('agent:create', cwd, mode, isolate, budget, kind),
  agentComputerContext: (): Promise<AiResult<ComputerContext>> => ipcRenderer.invoke('agent:computer-context'),
  agentSetBudget: (sid: string, amount: number | null): Promise<AiResult<AgentBudgetEvent>> => ipcRenderer.invoke('agent:budget', sid, amount),
  /** 在新分支上做时：跑测试、合并回你的代码；返回合并结果（一条 merge 事件） */
  agentMerge: (sid: string, runTests: boolean): Promise<AiResult<AgentEvent>> => ipcRenderer.invoke('agent:merge', sid, runTests),
  agentDiscard: (sid: string): Promise<AiResult<AgentEvent>> => ipcRenderer.invoke('agent:discard', sid),
  agentUndo: (sid: string, turnSeq: number): Promise<AiResult<AgentUndoEvent>> => ipcRenderer.invoke('agent:undo', sid, turnSeq),
  /** 仅主窗口：改会话标题、删除会话 */
  agentRename: (sid: string, title: string): Promise<AiResult<AgentSessionSummary>> => ipcRenderer.invoke('agent:rename', sid, title),
  agentDelete: (sid: string): Promise<AiResult<{ ok: boolean }>> => ipcRenderer.invoke('agent:delete', sid),
  agentCommands: (cwd: string | null, kind: AgentKind): Promise<AiResult<AgentCommandList>> =>
    ipcRenderer.invoke('agent:commands', cwd, kind),
  agentMemory: (cwd: string): Promise<AiResult<AgentMemoryNote[]>> => ipcRenderer.invoke('agent:memory', cwd),
  agentRemember: (cwd: string, text: string): Promise<AiResult<AgentMemoryNote & { added: boolean }>> =>
    ipcRenderer.invoke('agent:memory-add', cwd, text),
  agentForgetMemory: (cwd: string, id: string): Promise<AiResult<{ ok: boolean }>> => ipcRenderer.invoke('agent:memory-forget', cwd, id),
  /** 说一句话开始一轮；事件从 onAgentEvent 来 */
  agentSend: (sid: string, text: string, review = false, plan = false): void => ipcRenderer.send('agent:send', sid, text, review, plan),
  agentPlan: (sid: string, action: 'approve' | 'continue' | 'reject', pauseEach = false): Promise<AiResult<unknown>> =>
    ipcRenderer.invoke('agent:plan', sid, action, pauseEach),
  /** 接上一个会话：补发 after 之后的事件，正在做的话接着推 */
  agentWatch: (sid: string, after: number): void => ipcRenderer.send('agent:watch', sid, after),
  agentInterrupt: (sid: string): Promise<AiResult<unknown>> => ipcRenderer.invoke('agent:interrupt', sid),
  agentSetMode: (sid: string, mode: AgentMode): Promise<AiResult<unknown>> => ipcRenderer.invoke('agent:mode', sid, mode),
  agentCompact: (sid: string): Promise<AiResult<unknown>> => ipcRenderer.invoke('agent:compact', sid),
  /** 上下文账本：下一步发给模型的上下文由哪几块组成 */
  agentContext: (sid: string): Promise<AiResult<AgentLedger>> => ipcRenderer.invoke('agent:context', sid),
  agentConfirm: (id: string, approved: boolean, remember: boolean): Promise<boolean> =>
    ipcRenderer.invoke('agent:confirm', id, approved, remember),
  agentPickFolder: (): Promise<string | null> => ipcRenderer.invoke('agent:pick-folder'),
  agentTakeFocus: (): Promise<string | null> => ipcRenderer.invoke('agent:take-focus'),
  agentRules: (): Promise<AiResult<AgentRuleGroup[]>> => ipcRenderer.invoke('agent:rules'),
  agentMcp: (): Promise<AiResult<McpServerInfo[]>> => ipcRenderer.invoke('agent:mcp'),
  agentMcpPreview: (json: string): Promise<AiResult<McpPreviewItem[]>> => ipcRenderer.invoke('agent:mcp:preview', json),
  agentMcpImport: (json: string): Promise<AiResult<McpServerInfo[]>> => ipcRenderer.invoke('agent:mcp:import', json),
  agentMcpRestart: (name: string): Promise<AiResult<McpServerInfo[]>> => ipcRenderer.invoke('agent:mcp:restart', name),
  agentMcpDisable: (name: string, disabled: boolean): Promise<AiResult<McpServerInfo[]>> => ipcRenderer.invoke('agent:mcp:disable', name, disabled),
  agentMcpRemove: (name: string): Promise<AiResult<McpServerInfo[]>> => ipcRenderer.invoke('agent:mcp:remove', name),
  agentMcpForget: (name: string, tool: string): Promise<AiResult<McpServerInfo[]>> => ipcRenderer.invoke('agent:mcp:forget', name, tool),
  // 扩展包（只有主窗口能用）：pick 弹窗选包并检查，返回清单（取消了 data 是 null）
  extList: (): Promise<AiResult<ExtensionList>> => ipcRenderer.invoke('ext:list'),
  extPick: (kind: 'zip' | 'folder'): Promise<AiResult<{ token: string; plan: ExtensionPlan } | null>> => ipcRenderer.invoke('ext:pick', kind),
  extInstall: (token: string): Promise<AiResult<ExtensionList & { installed: string }>> => ipcRenderer.invoke('ext:install', token),
  extDiscard: (token: string): Promise<AiResult<{ ok: boolean }>> => ipcRenderer.invoke('ext:discard', token),
  extUpdate: (id: string, update: ExtensionUpdate): Promise<AiResult<ExtensionList>> => ipcRenderer.invoke('ext:update', id, update),
  extRemove: (id: string): Promise<AiResult<ExtensionList>> => ipcRenderer.invoke('ext:remove', id),
  agentForgetRule: (project: string, command: string): Promise<AiResult<unknown>> => ipcRenderer.invoke('agent:rules:forget', project, command),
  agentScreenshot: (sid: string, name: string): Promise<AiResult<string>> => ipcRenderer.invoke('agent:screenshot', sid, name),
  onAgentEvent: (cb: (sid: string, event: AgentEvent) => void): Unsubscribe => listen('agent:event', cb),
  onAgentStreamEnd: (cb: (sid: string, error?: string) => void): Unsubscribe => listen('agent:stream-end', cb),
  onAgentFocus: (cb: (sid: string) => void): Unsubscribe => listen('agent:focus', cb),
  /** 星图：她在哪个文件上干活（读的星球闪一下，改过的变绿） */
  onWorkbenchActivity: (cb: (a: AgentActivity) => void): Unsubscribe => listen('workbench:activity', cb),
  /** 角色窗口、星图：把任务交给工作窗口（开会话、开始做、打开窗口） */
  agentTask: (task: { text: string; project?: string; cwd?: string }): Promise<AiResult<AgentSessionSummary>> =>
    ipcRenderer.invoke('agent:task', task),
  /** 从任何窗口打开工作窗口（可以指定会话） */
  openAgent: (sid?: string): void => ipcRenderer.send('agent:open', sid),
  // 她的作品、日记（主窗口的作品页用；studioShow 是气泡里点「看看」）
  studioList: (): Promise<AiResult<Work[]>> => ipcRenderer.invoke('studio:list'),
  studioGet: (id: string): Promise<AiResult<WorkDetail>> => ipcRenderer.invoke('studio:get', id),
  studioDelete: (id: string): Promise<AiResult<{ ok: boolean }>> => ipcRenderer.invoke('studio:delete', id),
  studioOpen: (id: string): Promise<AiResult<{ ok: boolean }>> => ipcRenderer.invoke('studio:open', id),
  studioReveal: (id: string): Promise<AiResult<{ ok: boolean }>> => ipcRenderer.invoke('studio:reveal', id),
  studioNow: (idea?: { title: string; idea: string; kind: 'web' | 'script' }): Promise<AiResult<Work>> =>
    ipcRenderer.invoke('studio:now', idea ?? null),
  studioDiary: (): Promise<AiResult<DiaryEntry[]>> => ipcRenderer.invoke('studio:diary'),
  studioKnown: (): Promise<{ firstSeen?: string; days: number } | null> => ipcRenderer.invoke('studio:known'),
  studioShow: (id?: string): void => ipcRenderer.send('studio:show', id),

  // 代码库
  codeList: (): Promise<AiResult<CodeProject[]>> => ipcRenderer.invoke('code:list'),
  codeFixes: (): Promise<AiResult<FixStats>> => ipcRenderer.invoke('code:fixes'),
  /** 弹出选文件夹的对话框；取消了返回 null */
  codeAdd: (): Promise<AiResult<CodeProject> | null> => ipcRenderer.invoke('code:add'),
  codeRefresh: (id: string): Promise<AiResult<unknown>> => ipcRenderer.invoke('code:refresh', id),
  codeIgnore: (id: string, patterns: string[]): Promise<AiResult<CodeProject>> => ipcRenderer.invoke('code:ignore', id, patterns),
  codeRemove: (id: string): Promise<AiResult<unknown>> => ipcRenderer.invoke('code:remove', id),
  onKnowledgeBusy: (cb: (count: number) => void): Unsubscribe => listen('knowledge:busy', cb),

  // 桌面上的她：全屏时躲开、免打扰、省电、提醒（改偏好、免打扰、删提醒只认主窗口；「过会儿再提醒」只认角色窗口）
  desktopStatus: (): Promise<DesktopStatus | null> => ipcRenderer.invoke('desktop:status'),
  desktopSetPrefs: (prefs: Partial<DesktopPrefs>): Promise<DesktopStatus | null> => ipcRenderer.invoke('desktop:prefs', prefs),
  desktopSetDnd: (on: boolean): Promise<boolean> => ipcRenderer.invoke('desktop:dnd', on),
  reminderDelete: (id: string): Promise<boolean> => ipcRenderer.invoke('reminder:delete', id),
  // 拖给她的文件、剪贴板（只认角色窗口）：路径由 Electron 从拖进来的文件取，读文件只能读刚拖进来、看过一眼的
  pathForFile: (file: File): string => webUtils.getPathForFile(file),
  dropInspect: (paths: string[]): Promise<DropInfo[]> => ipcRenderer.invoke('drop:inspect', paths),
  dropRead: (path: string): Promise<string | null> => ipcRenderer.invoke('drop:read', path),
  clipboardText: (): Promise<string> => ipcRenderer.invoke('clipboard:text'),
  /** 右键菜单「看看剪贴板里的报错」 */
  onClipboardAsk: (cb: () => void): Unsubscribe => listen('clipboard:ask', cb),

  /** 让她现在说说今天做了什么（主窗口） */
  desktopReportNow: (): Promise<boolean> => ipcRenderer.invoke('desktop:report-now'),
  reminderSnooze: (text: string, minutes: number): Promise<boolean> => ipcRenderer.invoke('reminder:snooze', text, minutes),
  onDesktopStatus: (cb: (status: DesktopStatus) => void): Unsubscribe => listen('desktop:status', cb),

  // 主窗口：主进程要它切到某一页（右键菜单、星图、屏幕上的报错这些入口）
  onShellGo: (cb: (route: ShellRoute) => void): Unsubscribe => listen('shell:go', (raw: unknown) => cb(cleanRoute(raw))),

  // 设置（主窗口里的设置页）
  openSettings: (section?: SettingsSection): void => ipcRenderer.send('settings:open', section),
  aiSettings: (): Promise<AiResult<AiSettings>> => ipcRenderer.invoke('ai:settings'),
  aiSettingsPut: (update: AiSettingsUpdate): Promise<AiResult<AiSettings>> => ipcRenderer.invoke('ai:settings:put', update),
  aiSettingsTest: (): Promise<AiResult<{ ok: boolean; message: string }>> => ipcRenderer.invoke('ai:settings:test'),
  /** 技能包列表（带上用户的开关）；AI 服务没开时是 null */
  skillsList: (): Promise<SkillInfo[] | null> => ipcRenderer.invoke('skills:list'),
  skillsSet: (id: string, on: boolean): void => ipcRenderer.send('skills:set', id, on),
  modelPath: (): Promise<string | null> => ipcRenderer.invoke('model:path'),
  modelChoose: (): void => ipcRenderer.send('model:choose'),
  /** 换回自带的原创形象 */
  modelBuiltin: (): void => ipcRenderer.send('model:builtin'),
  onModelBuiltin: (cb: () => void): Unsubscribe => listen('model:builtin', cb),
  onModelPath: (cb: (path: string | null) => void): Unsubscribe => listen('model:path', cb),
  resetPosition: (): void => ipcRenderer.send('win:reset-position'),
  /** 在资源管理器里显示 AI 服务的配置、模型、数据文件夹（只认这几个名字，不接受任意路径） */
  revealPath: (key: 'env' | 'models' | 'data' | 'mcp'): void => ipcRenderer.send('ai:reveal', key),

  // 语音开关：角色窗口报告，设置窗口读写
  voiceReport: (prefs: VoicePrefs): void => ipcRenderer.send('voice:state', prefs),
  voiceGet: (): Promise<VoicePrefs> => ipcRenderer.invoke('voice:get'),
  voiceSet: (key: keyof VoicePrefs, on: boolean): void => ipcRenderer.send('voice:set', key, on),
  /** 看屏幕的状态和上次的统计 */
  screenStatus: (): Promise<AiResult<ScreenStatus>> => ipcRenderer.invoke('screen:status'),
  onVoiceState: (cb: (prefs: VoicePrefs) => void): Unsubscribe => listen('voice:state', cb),

  /** 设置窗口里做了某件事，桌面上的她做出反应 */
  onCharacterEvent: (cb: (ev: CharacterEvent) => void): Unsubscribe => listen('character:event', cb)
}

export type AilaApi = typeof api

contextBridge.exposeInMainWorld('aila', api)
