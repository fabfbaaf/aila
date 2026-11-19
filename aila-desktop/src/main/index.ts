import {
  app,
  BrowserWindow,
  clipboard,
  dialog,
  globalShortcut,
  ipcMain,
  Menu,
  nativeImage,
  safeStorage,
  screen,
  session,
  shell,
  Tray,
  type MenuItemConstructorOptions
} from 'electron'
import {
  addCodeProject,
  addKnowledge,
  codeChunk,
  codeGraph,
  codeLocateAll,
  codeOutline,
  codeProjectAction,
  setCodeIgnore,
  deleteKnowledge,
  forgetAgentRule,
  getAgentRules,
  getAiSettings,
  getFixStats,
  getScreenStatus,
  listCodeProjects,
  listKnowledge,
  listSkills,
  putAiSettings,
  testLlm
} from './ai'
import { dirname, join } from 'path'
import { existsSync, readFileSync, writeFileSync } from 'fs'
import WebSocket from 'ws'
import { AI_SERVICE_URL, answerConfirm, streamChat, type ChatMessage } from '../shared/chat'
import type { AuthForm, MemoryOp, StoredMessage } from '../shared/cloud'
import type { AiSettingsUpdate, CharacterEvent, SettingsSection, VoicePrefs } from '../shared/settings'
import type { PendingFix, WorkbenchFocus } from '../shared/workbench'
import type { AgentMode } from '../shared/agent'
import { routeForSection, toHash, type ShellRoute } from '../shared/shell'
import { trayIcon } from './trayIcon'
import { DesktopKeeper, type BriefingState } from './desktopKeeper'
import { Drops } from './drops'
import type { DesktopPrefs } from '../shared/desktop'
import { AgentRelay } from './agent'
import { lockDown } from './navigation'
import { validMcpName } from '../shared/mcp'
import { registerAgentBudgetIpc } from './agentBudgetIpc'
import { registerAgentScreenshotIpc } from './agentScreenshotIpc'
import { registerAgentCreateIpc } from './agentCreateIpc'
import type { VoiceMenuAction, VoiceMenuState } from '../shared/voice'
import { Account, type SecretBox } from './cloud/account'
import type { SocketLike } from './cloud/sync'
import { createServiceKeeper, serviceLocator } from './serviceNode'
import { ModelDownloader } from './models'
import { registerServiceIpc } from './serviceIpc'
import { openWorkPreview, revealWork, studio, WORK_ID } from './studio'
import type { LifeState } from '../shared/studio'

// 桌面窗口：透明、无边框、置顶，只在角色身上响应鼠标
const WIN_WIDTH = 300
const WIN_HEIGHT = 480
const SNAP_DISTANCE = 24
/** 随时叫出对话框的全局快捷键（A = Aila） */
const CHAT_SHORTCUT = 'Alt+Shift+A'
/** 不用唤醒词，直接开始听（V = Voice） */
const LISTEN_SHORTCUT = 'Alt+Shift+V'
/** 免打扰：把她藏起来 / 叫回来（H = Hide） */
const HIDE_SHORTCUT = 'Alt+Shift+H'
const AI_URL = process.env['AILA_AI_URL'] ?? AI_SERVICE_URL
const locateAi = serviceLocator({ packaged: app.isPackaged, appPath: app.getAppPath(), userData: app.getPath('userData') })
const serviceKeeper = createServiceKeeper({
  locate: locateAi,
  url: AI_URL,
  changed: (state) => broadcast('service:state', state)
})
// 设置页「语音」里的「下载语音模型」：进度只发给主窗口
const modelDownloader = new ModelDownloader({
  command: locateAi,
  changed: (state) => mainWin?.webContents.send('models:state', state),
  restart: () => serviceKeeper.restart()
})

// 开发调试：设置 AILA_REMOTE_DEBUG=端口 时打开远程调试，用脚本驱动真实窗口做端到端测试（打包后无效）
if (process.env['AILA_REMOTE_DEBUG'] && !app.isPackaged) {
  app.commandLine.appendSwitch('remote-debugging-port', process.env['AILA_REMOTE_DEBUG'])
  app.commandLine.appendSwitch('remote-debugging-address', '127.0.0.1')
}

let win: BrowserWindow | null = null
/**
 * 主窗口「艾拉」：任务（编程 Agent）、项目（含地图，原来的代码星图）、资料、记忆、设置都是它里面的页面。
 * 以前是工作窗口、星图、设置三个窗口；IPC 里原来「只认工作窗口 / 星图窗口」的地方都改成只认主窗口
 */
let mainWin: BrowserWindow | null = null
/** 设置、账号、记忆、资料、代码库这些写操作只认主窗口（设置都在主窗口里）；角色窗口出了问题也改不了密钥、删不了东西 */
const fromMainWindow = (e: { sender: Electron.WebContents }): boolean => !!mainWin && e.sender === mainWin.webContents
const MAIN_ONLY = { ok: false, error: '只有主窗口能用' }
/** 主窗口打开时要显示的会话（任务页加载好以后来取） */
let agentFocus: string | null = null
let tray: Tray | null = null
/** 全屏时躲开、免打扰、省电、提醒（见 desktopKeeper.ts） */
let desktop: DesktopKeeper | null = null
/** 拖到她身上的文件（只能读刚拖进来的） */
const drops = new Drops()
const AGENT_MODES: AgentMode[] = ['read_only', 'ask', 'auto']
const agentRelay = new AgentRelay(AI_URL, {
  emit: (sid, e) => mainWin?.webContents.send('agent:event', sid, e),
  ended: (sid, error) => mainWin?.webContents.send('agent:stream-end', sid, error),
  progress: (progress) => {
    characterEvent({ kind: 'agent', progress })
    // 要你确认、窗口又不在前面：任务栏图标闪一闪（点开窗口就不闪了）
    if (progress.kind === 'confirm' && mainWin && !mainWin.isFocused()) mainWin.flashFrame(true)
  },
  // 她在哪个文件上干活：星图里读的星球闪一下、改过的变绿
  activity: (a) => mainWin?.webContents.send('workbench:activity', a),
  // 工作窗口里等你确认的改文件操作：星图里也显示这份 diff，双手合十可以确认
  confirm: (c) => {
    if (c) pendingFix = { id: c.id, detail: c.detail, source: 'agent' }
    else if (pendingFix?.source === 'agent') pendingFix = null
    else return
    mainWin?.webContents.send('workbench:pending-fix', pendingFix)
  }
})
/** 打开星图时要定位的报错：页面加载好以后来取 */
let workbenchFocus: WorkbenchFocus | null = null
/** 角色窗口里正等用户点头的代码修改（星图里显示出来，双手合十可以确认） */
let pendingFix: PendingFix | null = null
let account: Account | null = null
/** 角色窗口里的语音开关，设置窗口要显示 */
let voicePrefs: VoicePrefs = { wake: false, speak: true, watch: false }
let cursorTimer: NodeJS.Timeout | null = null
let drag: { cursorX: number; cursorY: number; winX: number; winY: number } | null = null
const chats = new Map<number, AbortController>()

// ---------- 设置：记住上次加载的模型 ----------

interface Settings {
  modelPath?: string
  /** 桌面上的她的偏好（DesktopPrefs，读的时候再检查） */
  desktop?: unknown
  /** 今天见过面、说过日报没有 */
  briefing?: BriefingState
  /** 她的一天：第一次见面的日子、下一件作品什么时候做、哪天写过日记…… */
  life?: LifeState
}

const settingsFile = (): string => join(app.getPath('userData'), 'settings.json')

function loadSettings(): Settings {
  try {
    return JSON.parse(readFileSync(settingsFile(), 'utf-8')) as Settings
  } catch {
    return {}
  }
}

function saveSettings(s: Settings): void {
  writeFileSync(settingsFile(), JSON.stringify(s, null, 2), 'utf-8')
}

function readModel(path: string | undefined): ArrayBuffer | null {
  if (!path || !existsSync(path)) return null
  const buf = readFileSync(path)
  return buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength)
}

// ---------- 窗口 ----------

function createWindow(): void {
  const { workArea } = screen.getPrimaryDisplay()

  win = new BrowserWindow({
    width: WIN_WIDTH,
    height: WIN_HEIGHT,
    x: workArea.x + workArea.width - WIN_WIDTH - 40,
    y: workArea.y + workArea.height - WIN_HEIGHT,
    transparent: true,
    backgroundColor: '#00000000',
    frame: false,
    resizable: false,
    hasShadow: false,
    alwaysOnTop: true,
    skipTaskbar: true,
    show: false,
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      contextIsolation: true,
      sandbox: true
    }
  })

  // 默认鼠标穿透；渲染层发现鼠标在角色身上时再关闭穿透
  win.setIgnoreMouseEvents(true, { forward: true })
  win.setAlwaysOnTop(true, 'floating')

  win.once('ready-to-show', () => {
    if (desktop?.presence.visible ?? true) win?.showInactive()
  })

  loadPage(win)
  startCursorFeed()

  win.on('closed', () => {
    if (cursorTimer) clearInterval(cursorTimer)
    win = null
  })
}

/** 角色窗口和主窗口加载同一个页面，主窗口的地址后面带 #/页面（见 shared/shell.ts） */
function loadPage(w: BrowserWindow, hash = ''): void {
  const dev = process.env['ELECTRON_RENDERER_URL']
  if (dev) {
    // 开发时把渲染层的日志打印到终端，方便排查
    w.webContents.on('console-message', (_e, level, message) => console.log(`[renderer:${level}] ${message}`))
    void w.loadURL(dev + (hash ? `#${hash}` : ''))
  } else {
    void w.loadFile(join(__dirname, '../renderer/index.html'), hash ? { hash } : undefined)
  }
}

/** 打开主窗口并切到某一页；已经开着就切过去、拿到前面来 */
function openMain(route: ShellRoute = { page: 'tasks' }): void {
  if (mainWin) {
    mainWin.webContents.send('shell:go', route)
    if (mainWin.isMinimized()) mainWin.restore()
    mainWin.show()
    mainWin.focus()
    return
  }
  mainWin = new BrowserWindow({
    width: 1280,
    height: 820,
    minWidth: 900,
    minHeight: 560,
    title: '艾拉',
    backgroundColor: '#0f1117',
    autoHideMenuBar: true,
    show: false,
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      contextIsolation: true,
      sandbox: true
    }
  })
  mainWin.setMenu(null)
  mainWin.once('ready-to-show', () => mainWin?.show())
  // 关了窗口不停任务：AI 服务那边照样做完，做完她会说一声（事件流还在主进程里收）
  mainWin.on('closed', () => (mainWin = null))
  loadPage(mainWin, toHash(route))
}

/** 原来的设置分区：资料库、记忆、代码库各自成了一页，其余在设置页里 */
function openSettings(section: SettingsSection = 'model'): void {
  openMain(routeForSection(section))
}

/** 地图（原来的代码星图）：要定位的报错先记下，地图挂上时来取；开着的话也直接发过去 */
function openWorkbench(focus?: WorkbenchFocus): void {
  if (focus) workbenchFocus = focus
  openMain({ page: 'projects', tab: 'map' })
  if (focus) mainWin?.webContents.send('workbench:focus', focus)
}

/** 任务页：要看的会话先记下，任务页加载好时来取；开着的话也直接发过去 */
function openAgent(sid?: string): void {
  if (sid) agentFocus = sid
  openMain({ page: 'tasks' })
  if (sid) mainWin?.webContents.send('agent:focus', sid)
}

function cleanFocus(raw: unknown): WorkbenchFocus | undefined {
  if (!raw || typeof raw !== 'object') return undefined
  const f = raw as Record<string, unknown>
  return {
    project: typeof f.project === 'string' ? f.project.slice(0, 64) : undefined,
    error: typeof f.error === 'string' ? f.error.slice(0, 8000) : undefined
  }
}

/** 状态变化发给所有窗口（角色窗口和主窗口） */
function broadcast(channel: string, ...args: unknown[]): void {
  for (const w of BrowserWindow.getAllWindows()) w.webContents.send(channel, ...args)
}

/** 设置窗口里做了某件事，让桌面上的她有点反应 */
function characterEvent(ev: CharacterEvent): void {
  win?.webContents.send('character:event', ev)
}

// 窗口外的鼠标移动收不到事件，所以由主进程轮询光标位置推给渲染层（用于视线跟随）
function startCursorFeed(): void {
  let lastX = NaN
  let lastY = NaN
  cursorTimer = setInterval(() => {
    if (!win || !win.isVisible()) return
    const p = screen.getCursorScreenPoint()
    if (p.x === lastX && p.y === lastY) return
    lastX = p.x
    lastY = p.y
    const b = win.getBounds()
    win.webContents.send('cursor', { x: p.x - b.x, y: p.y - b.y })
  }, 33)
}

// 松手时如果靠近屏幕边缘，就贴过去
function snapToEdges(): void {
  if (!win) return
  const b = win.getBounds()
  const { workArea: wa } = screen.getDisplayMatching(b)
  let { x, y } = b
  if (Math.abs(b.x - wa.x) < SNAP_DISTANCE) x = wa.x
  if (Math.abs(wa.x + wa.width - (b.x + b.width)) < SNAP_DISTANCE) x = wa.x + wa.width - b.width
  if (Math.abs(wa.y + wa.height - (b.y + b.height)) < SNAP_DISTANCE) y = wa.y + wa.height - b.height
  if (Math.abs(b.y - wa.y) < SNAP_DISTANCE) y = wa.y
  win.setPosition(x, y)
}

async function chooseModel(parent: BrowserWindow | null = win): Promise<void> {
  if (!win || !parent) return
  const result = await dialog.showOpenDialog(parent, {
    title: '选择 VRM 模型',
    filters: [{ name: 'VRM 模型', extensions: ['vrm'] }],
    properties: ['openFile']
  })
  if (result.canceled || result.filePaths.length === 0) return
  const path = result.filePaths[0]
  const data = readModel(path)
  if (!data) return
  saveSettings({ ...loadSettings(), modelPath: path })
  win.webContents.send('model:loaded', data)
  broadcast('model:path', path)
}

function resetPosition(): void {
  if (!win) return
  const { workArea } = screen.getPrimaryDisplay()
  win.setPosition(workArea.x + workArea.width - WIN_WIDTH - 40, workArea.y + workArea.height - WIN_HEIGHT)
}

function openChat(): void {
  if (!win) return
  desktop?.setDnd(false)
  win.focus() // 窗口平时不抢焦点，要打字时才拿过来
  win.webContents.send('chat:open')
}

function voiceMenu(action: VoiceMenuAction): void {
  if (action === 'listen') desktop?.setDnd(false)
  win?.webContents.send('voice:menu', action)
}

/** 免打扰开关（右键菜单、托盘、快捷键、设置页都走这里） */
function toggleDnd(on = !desktop?.dnd): void {
  desktop?.setDnd(on)
  updateTray()
}

// ---------- 麦克风权限：只给自己的页面 ----------

function isOwnPage(url: string): boolean {
  const dev = process.env['ELECTRON_RENDERER_URL']
  return dev ? url.startsWith(dev) : url.startsWith('file://')
}

function restrictPermissions(): void {
  const ses = session.defaultSession
  ses.setPermissionRequestHandler((_wc, permission, callback, details) => {
    callback(permission === 'media' && isOwnPage(details.requestingUrl))
  })
  ses.setPermissionCheckHandler((_wc, permission, origin) => permission === 'media' && isOwnPage(origin))
}

// ---------- 账号和同步 ----------

/** 刷新令牌用系统的加密存储保存（Windows 上是 DPAPI），别的程序和别的系统用户读不出来 */
function secretBox(): SecretBox {
  if (safeStorage.isEncryptionAvailable()) {
    return {
      encrypt: (text) => 'v1:' + safeStorage.encryptString(text).toString('base64'),
      decrypt: (data) => {
        if (!data.startsWith('v1:')) throw new Error('无法识别的令牌格式')
        return safeStorage.decryptString(Buffer.from(data.slice(3), 'base64'))
      }
    }
  }
  console.warn('这台电脑不支持加密存储，登录令牌将以明文保存在用户目录')
  return {
    encrypt: (text) => 'plain:' + text,
    decrypt: (data) => {
      if (!data.startsWith('plain:')) throw new Error('无法识别的令牌格式')
      return data.slice(6)
    }
  }
}

function createAccount(): Account {
  return new Account({
    dataDir: app.getPath('userData'),
    box: secretBox(),
    openSocket: (url, token) =>
      new WebSocket(url, { headers: { Authorization: `Bearer ${token}` } }) as unknown as SocketLike,
    onStatus: (status) => broadcast('account:status', status),
    onRemote: (changes) => {
      win?.webContents.send('history:remote', changes)
      mainWin?.webContents.send('history:changed')
    },
    onMemories: (list) => broadcast('memory:changed', list),
    extractMemories: async (req) => {
      const res = await fetch(`${AI_URL}/memory/extract`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(req),
        signal: AbortSignal.timeout(60_000)
      })
      if (!res.ok) throw new Error(`AI 服务返回 ${res.status}`)
      return ((await res.json()) as { ops: MemoryOp[] }).ops
    }
  })
}

/** 渲染层传来的消息做一下基本检查再存 */
function isStoredMessage(m: unknown): m is StoredMessage {
  const x = m as StoredMessage
  return (
    typeof x?.id === 'string' &&
    x.id.length <= 64 &&
    (x.role === 'user' || x.role === 'assistant') &&
    typeof x.content === 'string' &&
    x.content.length <= 8000 &&
    typeof x.createdAt === 'string'
  )
}

// ---------- 语音合成：和对话一样由主进程转发 ----------

async function synthesize(text: string): Promise<ArrayBuffer | null> {
  const res = await fetch(`${AI_URL}/tts`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ text })
  })
  if (res.status === 204) return null
  if (!res.ok) throw new Error(`语音合成返回 ${res.status}`)
  return res.arrayBuffer()
}

/** 提前加载语音模型（开着「说话」「唤醒」时）：AI 服务启动时不加载这两个大模型 */
async function voiceWarmup(parts: { tts: boolean; ears: boolean }): Promise<void> {
  await fetch(`${AI_URL}/voice/warmup`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(parts)
  }).catch(() => undefined) // 服务还没起来也没关系：第一次用到时会自己加载
}

// ---------- 对话：转发给本地 AI 服务 ----------
// 由主进程发请求，渲染层不用放开 CSP 和跨域，打包后也一样能用

async function relayChat(id: number, messages: ChatMessage[]): Promise<void> {
  const controller = new AbortController()
  chats.set(id, controller)
  try {
    // 带上她记得的事，AI 服务会挑出和这次话题相关的几条
    // 还有用户对技能包的开关：关掉的技能包里的工具不会给大模型
    const extra = { memories: account?.memoriesForChat() ?? [], skills: account?.skillOverrides() ?? {} }
    await streamChat(
      AI_URL,
      messages,
      controller.signal,
      (ev) => {
        // 她在对话里把编程任务交给了工作窗口：打开工作窗口、接上那个任务
        if (ev.type === 'agent_task') openAgent(ev.session)
        // 她记下了一个提醒：存起来，到点叫你。没记上（时间不对、太多了）让她说一声
        if (ev.type === 'reminder' && desktop) {
          const r = desktop.addReminder(String(ev.at).slice(0, 64), String(ev.text).slice(0, 500))
          if (typeof r === 'string') characterEvent({ kind: 'reminder-failed', why: r })
        }
        win?.webContents.send('chat:event', id, ev)
      },
      extra
    )
  } finally {
    chats.delete(id)
  }
}

// ---------- IPC ----------

function registerIpc(): void {
  ipcMain.on('win:set-ignore-mouse', (_e, ignore: boolean) => {
    win?.setIgnoreMouseEvents(ignore, { forward: true })
  })

  ipcMain.on('win:drag-start', () => {
    if (!win) return
    const c = screen.getCursorScreenPoint()
    const [winX, winY] = win.getPosition()
    drag = { cursorX: c.x, cursorY: c.y, winX, winY }
  })

  ipcMain.on('win:drag-move', () => {
    if (!win || !drag) return
    const c = screen.getCursorScreenPoint()
    win.setPosition(drag.winX + c.x - drag.cursorX, drag.winY + c.y - drag.cursorY)
  })

  ipcMain.on('win:drag-end', () => {
    drag = null
    snapToEdges()
  })

  ipcMain.on('win:focus', () => win?.focus())
  ipcMain.on('win:blur', () => win?.blur())

  ipcMain.on('chat:start', (_e, id: number, messages: ChatMessage[]) => void relayChat(id, messages))
  ipcMain.on('chat:cancel', (_e, id: number) => chats.get(id)?.abort())
  // 主窗口的对话记录页点「和她说话」：在桌面上打开对话框（只是打开，不代替用户说什么）
  ipcMain.on('chat:open-request', (e) => {
    if (mainWin && e.sender === mainWin.webContents) openChat()
  })
  // 「好 / 不要」只认角色窗口发来的（设置窗口和别的页面不能替用户点头）
  ipcMain.handle('chat:confirm', (e, id: unknown, approved: unknown) => {
    if (e.sender !== win?.webContents || typeof id !== 'string' || id.length > 64 || typeof approved !== 'boolean') {
      return false
    }
    return answerConfirm(AI_URL, id, approved)
  })
  ipcMain.handle('tts:synthesize', (_e, text: string) => synthesize(text))
  ipcMain.handle('voice:warmup', (_e, parts: unknown) => {
    const p = (parts ?? {}) as { tts?: unknown; ears?: unknown }
    return voiceWarmup({ tts: p.tts === true, ears: p.ears === true })
  })

  ipcMain.handle('history:load', () => account?.history.visible() ?? [])
  ipcMain.on('history:append', (_e, m: unknown) => {
    if (isStoredMessage(m)) {
      account?.append({ id: m.id, role: m.role, content: m.content, createdAt: m.createdAt })
      mainWin?.webContents.send('history:changed')
    }
  })
  ipcMain.on('settings:put', (_e, key: string, value: unknown) => account?.putSetting(key, value))
  ipcMain.handle('account:status', () => account?.status())
  ipcMain.handle('account:details', (e) => (fromMainWindow(e) ? account?.details() : undefined))
  ipcMain.handle('account:auth', (e, form: AuthForm) =>
    !fromMainWindow(e) ? MAIN_ONLY : account ? account.authenticate(form) : { ok: false, error: '还没准备好' }
  )
  ipcMain.handle('account:logout', (e) => (fromMainWindow(e) ? account?.logout() : undefined))

  ipcMain.handle('memory:list', () => account?.memories.list() ?? [])
  ipcMain.on('memory:delete', (e, id: string) => {
    if (!fromMainWindow(e)) return
    account?.deleteMemory(String(id))
    characterEvent({ kind: 'forget-one' })
  })
  ipcMain.handle('memory:forget-all', (e) => {
    if (!fromMainWindow(e)) return 0
    const n = account?.forgetEverything() ?? 0
    if (n) characterEvent({ kind: 'forget-all' })
    return n
  })

  ipcMain.handle('knowledge:list', () => listKnowledge(AI_URL))
  ipcMain.handle('knowledge:delete', (e, id: string) => (fromMainWindow(e) ? deleteKnowledge(AI_URL, String(id)) : MAIN_ONLY))
  ipcMain.handle('knowledge:add', async (e) => {
    if (!fromMainWindow(e)) return null
    const parent = BrowserWindow.fromWebContents(e.sender)
    if (!parent) return null
    const picked = await dialog.showOpenDialog(parent, {
      title: '添加资料（只保存在这台电脑上）',
      filters: [{ name: '文档', extensions: ['txt', 'md', 'markdown', 'pdf', 'docx', 'html', 'htm'] }],
      properties: ['openFile', 'multiSelections']
    })
    if (picked.canceled || picked.filePaths.length === 0) return null
    e.sender.send('knowledge:busy', picked.filePaths.length)
    const result = await addKnowledge(AI_URL, picked.filePaths)
    if (result.added.length) characterEvent({ kind: 'knowledge-added', names: result.added.map((d) => d.name) })
    return result
  })

  // 代码库：选文件夹由主进程弹窗，渲染层不能指定任意路径
  ipcMain.handle('code:list', () => listCodeProjects(AI_URL))
  ipcMain.handle('code:fixes', () => getFixStats(AI_URL))
  ipcMain.handle('code:add', async (e) => {
    if (!fromMainWindow(e)) return null
    const parent = BrowserWindow.fromWebContents(e.sender)
    if (!parent) return null
    const picked = await dialog.showOpenDialog(parent, {
      title: '选一个项目文件夹（代码只在这台电脑上建索引）',
      properties: ['openDirectory']
    })
    if (picked.canceled || picked.filePaths.length === 0) return null
    return addCodeProject(AI_URL, picked.filePaths[0])
  })
  ipcMain.handle('code:refresh', (e, id: string) => (fromMainWindow(e) ? codeProjectAction(AI_URL, String(id), 'refresh') : MAIN_ONLY))
  ipcMain.handle('code:ignore', (e, id: unknown, patterns: unknown) =>
    fromMainWindow(e) && typeof id === 'string' && Array.isArray(patterns) && patterns.length <= 100 && patterns.every((p) => typeof p === 'string' && p.length <= 200)
      ? setCodeIgnore(AI_URL, id, patterns as string[])
      : MAIN_ONLY
  )

  // 代码星图
  ipcMain.handle('code:graph', (_e, id: string) => codeGraph(AI_URL, String(id)))
  ipcMain.handle('code:outline', (_e, id: string, file: string) => codeOutline(AI_URL, String(id), String(file)))
  ipcMain.handle('code:chunk', (_e, id: string, file: string, line: number) =>
    codeChunk(AI_URL, String(id), String(file), Number(line) || 1)
  )
  ipcMain.handle('code:locate-all', (_e, text: string) => codeLocateAll(AI_URL, String(text).slice(0, 20000)))
  ipcMain.on('workbench:open', (_e, focus: unknown) => openWorkbench(cleanFocus(focus)))
  ipcMain.handle('workbench:take-focus', () => {
    const f = workbenchFocus
    workbenchFocus = null
    return f
  })
  // 在地图里点「问她」：只认主窗口发来的，交给桌面上的她去说
  ipcMain.on('chat:say', (e, text: unknown) => {
    if (e.sender !== mainWin?.webContents || typeof text !== 'string' || !text.trim()) return
    win?.webContents.send('chat:say', text.slice(0, 8000))
  })
  // 编程 Agent：只认主窗口发来的（角色窗口不能替你开会话、点「可以」）
  const fromAgent = (e: { sender: Electron.WebContents }): boolean => !!mainWin && e.sender === mainWin.webContents
  registerServiceIpc(ipcMain, serviceKeeper, fromAgent)
  ipcMain.handle('models:status', (e) => fromAgent(e) ? modelDownloader.status() : null)
  ipcMain.handle('models:download', (e) => fromAgent(e) ? modelDownloader.start() : { ok: false, error: '只有主窗口能用' })
  const sidOk = (sid: unknown): sid is string => typeof sid === 'string' && /^[\w-]{1,64}$/.test(sid)
  const notAllowed = { ok: false, error: '只有主窗口能用' }
  ipcMain.handle('agent:list', (e, q: unknown) =>
    fromAgent(e) ? agentRelay.list(typeof q === 'string' ? q.slice(0, 200) : '') : notAllowed
  )
  ipcMain.handle('agent:get', (e, sid: unknown) => (fromAgent(e) && sidOk(sid) ? agentRelay.get(sid) : notAllowed))
  registerAgentCreateIpc(ipcMain, agentRelay, fromAgent)
  registerAgentBudgetIpc(ipcMain, agentRelay, fromAgent, sidOk)
  ipcMain.handle('agent:merge', (e, sid: unknown, runTests: unknown) =>
    fromAgent(e) && sidOk(sid) ? agentRelay.merge(sid, runTests !== false) : notAllowed
  )
  ipcMain.handle('agent:discard', (e, sid: unknown) => (fromAgent(e) && sidOk(sid) ? agentRelay.discard(sid) : notAllowed))
  ipcMain.handle('agent:undo', (e, sid: unknown, turnSeq: unknown) =>
    fromAgent(e) && sidOk(sid) && typeof turnSeq === 'number' && Number.isSafeInteger(turnSeq) && turnSeq >= 0
      ? agentRelay.undo(sid, turnSeq)
      : notAllowed
  )
  ipcMain.handle('agent:rename', (e, sid: unknown, title: unknown) => {
    if (!fromAgent(e) || !sidOk(sid)) return notAllowed
    if (typeof title !== 'string' || !title.trim() || Array.from(title).length > 100) {
      return { ok: false, error: '标题要有 1–100 个字。' }
    }
    return agentRelay.rename(sid, title)
  })
  ipcMain.handle('agent:delete', (e, sid: unknown) => (fromAgent(e) && sidOk(sid) ? agentRelay.delete(sid) : notAllowed))
  // 项目记忆：写进去的话以后每个会话都会带上，所以也只认主窗口
  const cwdOk = (cwd: unknown): cwd is string => typeof cwd === 'string' && cwd.length > 0 && cwd.length <= 1000
  ipcMain.handle('agent:commands', (e, cwd: unknown, kind: unknown) =>
    fromAgent(e) && (cwd === null || cwdOk(cwd)) && (kind === 'project' || kind === 'computer')
      ? agentRelay.commands(cwd, kind)
      : notAllowed
  )
  ipcMain.handle('agent:memory', (e, cwd: unknown) => (fromAgent(e) && cwdOk(cwd) ? agentRelay.memory(cwd) : notAllowed))
  ipcMain.handle('agent:memory-add', (e, cwd: unknown, text: unknown) => {
    if (!fromAgent(e) || !cwdOk(cwd)) return notAllowed
    if (typeof text !== 'string' || !text.trim() || text.length > 2000) return { ok: false, error: '要记的话不能是空的，也别太长。' }
    return agentRelay.addMemory(cwd, text)
  })
  ipcMain.handle('agent:memory-forget', (e, cwd: unknown, id: unknown) =>
    fromAgent(e) && cwdOk(cwd) && typeof id === 'string' && /^[\w-]{1,64}$/.test(id) ? agentRelay.forgetMemory(cwd, id) : notAllowed
  )
  ipcMain.on('agent:send', (e, sid: unknown, text: unknown, review: unknown, plan: unknown) => {
    if (!fromAgent(e) || !sidOk(sid) || typeof text !== 'string') return
    // /review 可以不带话；普通的一句话、出方案的目标不能是空的
    if (review === true) agentRelay.send(sid, text.slice(0, 20000), true)
    else if (text.trim()) agentRelay.send(sid, text.slice(0, 20000), false, plan === true)
  })
  // 长任务：批准并开始做、停下以后接着做、不要这份计划
  ipcMain.handle('agent:plan', (e, sid: unknown, action: unknown, pauseEach: unknown) => {
    if (!fromAgent(e) || !sidOk(sid)) return notAllowed
    if (action === 'approve') agentRelay.planApprove(sid, pauseEach === true)
    else if (action === 'continue') agentRelay.planContinue(sid)
    else if (action === 'reject') return agentRelay.planReject(sid)
    else return notAllowed
    return { ok: true }
  })
  ipcMain.on('agent:watch', (e, sid: unknown, after: unknown) => {
    if (fromAgent(e) && sidOk(sid)) agentRelay.watch(sid, Number.isInteger(after) ? (after as number) : -1)
  })
  ipcMain.handle('agent:interrupt', (e, sid: unknown) => (fromAgent(e) && sidOk(sid) ? agentRelay.interrupt(sid) : notAllowed))
  ipcMain.handle('agent:mode', (e, sid: unknown, mode: unknown) =>
    fromAgent(e) && sidOk(sid) && AGENT_MODES.includes(mode as AgentMode) ? agentRelay.setMode(sid, mode as AgentMode) : notAllowed
  )
  ipcMain.handle('agent:compact', (e, sid: unknown) => (fromAgent(e) && sidOk(sid) ? agentRelay.compact(sid) : notAllowed))
  ipcMain.handle('agent:context', (e, sid: unknown) => (fromAgent(e) && sidOk(sid) ? agentRelay.context(sid) : notAllowed))
  ipcMain.handle('agent:rules', (e) => (fromAgent(e) ? getAgentRules(AI_URL) : notAllowed))
  // MCP 外部工具服务：加服务就是让这台电脑跑一个程序，只认主窗口
  const mcpJson = (json: unknown): json is string => typeof json === 'string' && json.length >= 2 && json.length <= 50000
  ipcMain.handle('agent:mcp', (e) => (fromAgent(e) ? agentRelay.mcp() : notAllowed))
  ipcMain.handle('agent:mcp:preview', (e, json: unknown) => (fromAgent(e) && mcpJson(json) ? agentRelay.mcpPreview(json) : notAllowed))
  ipcMain.handle('agent:mcp:import', (e, json: unknown) => (fromAgent(e) && mcpJson(json) ? agentRelay.mcpImport(json) : notAllowed))
  ipcMain.handle('agent:mcp:restart', (e, name: unknown) => (fromAgent(e) && validMcpName(name) ? agentRelay.mcpRestart(name) : notAllowed))
  ipcMain.handle('agent:mcp:disable', (e, name: unknown, disabled: unknown) =>
    fromAgent(e) && validMcpName(name) && typeof disabled === 'boolean' ? agentRelay.mcpDisable(name, disabled) : notAllowed
  )
  ipcMain.handle('agent:mcp:remove', (e, name: unknown) => (fromAgent(e) && validMcpName(name) ? agentRelay.mcpRemove(name) : notAllowed))
  ipcMain.handle('agent:mcp:forget', (e, name: unknown, tool: unknown) =>
    fromAgent(e) && validMcpName(name) && typeof tool === 'string' && tool.length > 0 && tool.length <= 200
      ? agentRelay.mcpForget(name, tool)
      : notAllowed
  )
  ipcMain.handle('agent:rules:forget', (e, project: unknown, command: unknown) => {
    if (!fromAgent(e)) return notAllowed
    if (typeof project !== 'string' || !project.trim() || project.length > 1000 ||
        typeof command !== 'string' || !command.trim() || command.length > 20000) {
      return { ok: false, error: '项目路径和命令要是非空文字，路径最多 1000 字，命令最多 20000 字。' }
    }
    return forgetAgentRule(AI_URL, project, command)
  })
  registerAgentScreenshotIpc(ipcMain, fromAgent, async () => (await getAiSettings(AI_URL)).data?.paths.data)
  ipcMain.handle('agent:confirm', (e, id: unknown, approved: unknown, remember: unknown) => {
    if (!fromAgent(e) || typeof id !== 'string' || id.length > 64 || typeof approved !== 'boolean') return false
    return agentRelay.confirm(id, approved, remember === true)
  })
  ipcMain.handle('agent:pick-folder', async (e) => {
    if (!fromAgent(e) || !mainWin) return null
    const res = await dialog.showOpenDialog(mainWin, { title: '选一个项目文件夹', properties: ['openDirectory'] })
    return res.canceled ? null : (res.filePaths[0] ?? null)
  })
  ipcMain.handle('agent:take-focus', (e) => {
    if (!fromAgent(e)) return null
    const sid = agentFocus
    agentFocus = null
    return sid
  })
  // 从别处（菜单、设置、地图）打开任务页
  ipcMain.on('agent:open', (_e, sid: unknown) => openAgent(sidOk(sid) ? sid : undefined))
  // 角色窗口（屏幕上的报错「交给她修」）、地图（「让她修」）交过来的任务：开会话、开始做、打开任务页
  ipcMain.handle('agent:task', async (e, raw: unknown) => {
    const fromHere = e.sender === win?.webContents || e.sender === mainWin?.webContents
    const t = (raw ?? {}) as { text?: unknown; project?: unknown; cwd?: unknown }
    if (!fromHere || typeof t.text !== 'string' || !t.text.trim()) return notAllowed
    const r = await agentRelay.task({
      text: t.text.slice(0, 20000),
      project: typeof t.project === 'string' ? t.project.slice(0, 64) : undefined,
      cwd: typeof t.cwd === 'string' ? t.cwd.slice(0, 1000) : undefined
    })
    if (r.ok && r.data) openAgent(r.data.id)
    return r
  })

  // 角色窗口报告：有没有等用户点头的代码修改（只认角色窗口）
  ipcMain.on('fix:pending', (e, fix: unknown) => {
    if (e.sender !== win?.webContents) return
    const f = fix as PendingFix | null
    const next = f && typeof f.id === 'string' && typeof f.detail === 'string' ? { id: f.id, detail: f.detail, source: 'chat' as const } : null
    if (!next && pendingFix?.source === 'agent') return // 角色窗口没有要确认的，不影响工作窗口那边的
    pendingFix = next
    mainWin?.webContents.send('workbench:pending-fix', pendingFix)
  })
  ipcMain.handle('workbench:pending-fix', () => pendingFix)
  // 地图里双手合十：只认主窗口，而且只能确认它正显示着的那份修改。
  // 对话里的修改由角色窗口去回答「好」；工作窗口里的改文件操作直接回答 AI 服务（id 要对得上正在等的那个）
  ipcMain.handle('gesture:seal', async (e, id: unknown) => {
    if (e.sender !== mainWin?.webContents || !pendingFix || pendingFix.id !== id) return false
    if (pendingFix.source === 'agent') return agentRelay.approvePending(pendingFix.id)
    win?.webContents.send('gesture:approve', pendingFix.id)
    return true
  })
  ipcMain.handle('code:remove', (e, id: string) => (fromMainWindow(e) ? codeProjectAction(AI_URL, String(id), 'delete') : MAIN_ONLY))

  // 设置（主窗口里的页面）
  ipcMain.on('settings:open', (_e, section?: SettingsSection) => openSettings(section))
  ipcMain.handle('ai:settings', () => getAiSettings(AI_URL))
  ipcMain.handle('ai:settings:put', async (e, update: AiSettingsUpdate) => {
    if (!fromMainWindow(e)) return MAIN_ONLY // 能改大模型地址和密钥：只认主窗口
    const res = await putAiSettings(AI_URL, update)
    if (res.ok) characterEvent({ kind: 'settings-saved' })
    return res
  })
  ipcMain.handle('ai:settings:test', (e) => (fromMainWindow(e) ? testLlm(AI_URL) : MAIN_ONLY))
  ipcMain.handle('skills:list', () => listSkills(AI_URL, account?.skillOverrides() ?? {}))
  ipcMain.on('skills:set', (e, id: string, on: boolean) => {
    if (fromMainWindow(e)) account?.setSkill(String(id), Boolean(on))
  })
  ipcMain.handle('model:path', () => loadSettings().modelPath ?? null)
  ipcMain.on('model:choose', (e) => void chooseModel(BrowserWindow.fromWebContents(e.sender)))
  // 换回她自己的原创形象：忘掉选过的模型文件，角色窗口加载自带的那个
  ipcMain.on('model:builtin', () => {
    const { modelPath: _, ...rest } = loadSettings()
    saveSettings(rest)
    win?.webContents.send('model:builtin')
    broadcast('model:path', null)
  })
  ipcMain.on('win:reset-position', resetPosition)
  ipcMain.on('ai:reveal', async (_e, key: string) => {
    // 路径从 AI 服务的设置里取，渲染层只能说「哪一个」，不能指定任意路径
    const res = await getAiSettings(AI_URL)
    const paths = res.data?.paths as Record<string, string> | undefined
    const path = paths && Object.hasOwn(paths, key) ? paths[key] : null
    if (!path) return
    if (existsSync(path)) shell.showItemInFolder(path)
    else void shell.openPath(dirname(path)) // 文件还没建（比如 mcp.json），打开所在的文件夹
  })

  // 语音和看屏幕的开关存在角色窗口里：它报告状态，设置窗口来读、来改
  ipcMain.on('voice:state', (_e, prefs: VoicePrefs) => {
    voicePrefs = { wake: Boolean(prefs.wake), speak: Boolean(prefs.speak), watch: Boolean(prefs.watch) }
    broadcast('voice:state', voicePrefs)
  })
  ipcMain.handle('voice:get', () => voicePrefs)
  ipcMain.on('voice:set', (_e, key: keyof VoicePrefs, on: boolean) => {
    const actions: Record<keyof VoicePrefs, VoiceMenuAction> = { wake: 'toggle-wake', speak: 'toggle-speak', watch: 'toggle-watch' }
    if (Object.hasOwn(actions, key) && voicePrefs[key] !== on) voiceMenu(actions[key])
  })
  ipcMain.handle('screen:status', () => getScreenStatus(AI_URL))

  // 桌面上的她：状态谁都能读；改偏好、免打扰、删提醒只认主窗口；「过 10 分钟再提醒」只认角色窗口（气泡里的按钮）
  const fromMain = (e: { sender: Electron.WebContents }): boolean => !!mainWin && e.sender === mainWin.webContents
  ipcMain.handle('desktop:status', () => desktop?.status() ?? null)
  ipcMain.handle('desktop:prefs', (e, prefs: unknown) => (fromMain(e) && desktop ? desktop.setPrefs(prefs) : null))
  ipcMain.handle('desktop:dnd', (e, on: unknown) => {
    if (!fromMain(e) || typeof on !== 'boolean') return false
    toggleDnd(on)
    return true
  })
  // 拖给她的文件、剪贴板：只认角色窗口（她身上才能拖、对她说才会去看）
  const fromCharacter = (e: { sender: Electron.WebContents }): boolean => !!win && e.sender === win.webContents
  // 她的作品、日记：看、删、打开、现在就做一件只认主窗口；气泡里点「看看」从角色窗口来，只是打开作品页
  const workOk = (id: unknown): id is string => typeof id === 'string' && WORK_ID.test(id)
  const nope = { ok: false, error: '只有主窗口能用' }
  ipcMain.handle('studio:list', (e) => (fromMain(e) ? studio.works(AI_URL) : nope))
  ipcMain.handle('studio:get', (e, id: unknown) => (fromMain(e) && workOk(id) ? studio.work(AI_URL, id) : nope))
  ipcMain.handle('studio:delete', (e, id: unknown) => (fromMain(e) && workOk(id) ? studio.remove(AI_URL, id) : nope))
  ipcMain.handle('studio:open', (e, id: unknown) => (fromMain(e) && workOk(id) ? openWorkPreview(AI_URL, id, mainWin) : nope))
  ipcMain.handle('studio:reveal', (e, id: unknown) => (fromMain(e) && workOk(id) ? revealWork(AI_URL, id) : nope))
  ipcMain.handle('studio:now', (e, idea: unknown) => {
    if (!fromMain(e) || !desktop) return nope
    const i = idea as Record<string, unknown> | null
    const valid =
      i && typeof i.title === 'string' && typeof i.idea === 'string' && i.title.trim() && i.idea.trim() && i.title.length <= 40 && i.idea.length <= 300
    return desktop.workNow(
      valid ? { title: String(i.title).trim(), idea: String(i.idea).trim(), kind: i.kind === 'script' ? 'script' : 'web', why: '' } : undefined
    )
  })
  ipcMain.handle('studio:diary', (e) => (fromMain(e) ? studio.diary(AI_URL) : nope))
  ipcMain.handle('studio:known', (e) => (fromMain(e) && desktop ? desktop.knownSince() : null))
  ipcMain.on('studio:show', (e, id: unknown) => {
    if (fromCharacter(e)) openMain({ page: 'studio', ...(workOk(id) ? { work: id } : {}) })
  })
  ipcMain.handle('drop:inspect', (e, paths: unknown) => {
    if (!fromCharacter(e) || !Array.isArray(paths)) return []
    return drops.inspect(paths.filter((p): p is string => typeof p === 'string' && p.length > 0 && p.length <= 1000))
  })
  ipcMain.handle('drop:read', (e, path: unknown) => (fromCharacter(e) && typeof path === 'string' ? drops.read(path) : null))
  ipcMain.handle('clipboard:text', (e) => (fromCharacter(e) ? clipboard.readText().slice(0, 20000) : ''))
  ipcMain.handle('desktop:report-now', (e) => (fromMain(e) ? (desktop?.reportNow() ?? false) : false))
  ipcMain.handle('reminder:delete', (e, id: unknown) =>
    fromMain(e) && typeof id === 'string' && id.length <= 64 ? (desktop?.removeReminder(id) ?? false) : false
  )
  ipcMain.handle('reminder:snooze', (e, text: unknown, minutes: unknown) => {
    if (e.sender !== win?.webContents || typeof text !== 'string' || !desktop) return false
    const m = Number(minutes)
    if (!Number.isInteger(m) || m < 1 || m > 120) return false
    return typeof desktop.snooze(text.slice(0, 500), m) !== 'string'
  })

  ipcMain.on('menu:show', async (_e, voice: VoiceMenuState) => {
    if (!win) return
    // 技能包列表从 AI 服务实时取（接进来的 MCP 服务也会出现在这里）
    const skills = await listSkills(AI_URL, account?.skillOverrides() ?? {})
    const skillItems: MenuItemConstructorOptions[] = skills
      ? skills.map((s) => ({
          label: s.available ? s.name : `${s.name}（${s.id === 'knowledge' ? '还没有资料' : '暂不可用'}）`,
          type: 'checkbox',
          checked: s.enabled,
          toolTip: s.description,
          click: () => account?.setSkill(s.id, !s.enabled)
        }))
      : [{ label: '（AI 服务没开）', enabled: false }]
    if (!win) return
    Menu.buildFromTemplate([
      { label: '和她说话', accelerator: CHAT_SHORTCUT, click: openChat },
      { label: '听我说', accelerator: LISTEN_SHORTCUT, click: () => voiceMenu('listen') },
      { type: 'separator' },
      { label: '语音唤醒「你好艾拉」', type: 'checkbox', checked: voice.wake, click: () => voiceMenu('toggle-wake') },
      { label: '出声说话', type: 'checkbox', checked: voice.speak, click: () => voiceMenu('toggle-speak') },
      {
        label: '留意屏幕上的报错',
        type: 'checkbox',
        checked: voice.watch,
        toolTip: '只看写代码的窗口（编辑器、终端），看到报错先问你',
        click: () => voiceMenu('toggle-watch')
      },
      { type: 'separator' },
      { label: '技能包', submenu: skillItems },
      { label: '打开艾拉', click: () => openMain({ page: 'tasks' }) },
      { label: '项目和地图…', click: () => openMain({ page: 'projects', tab: 'map' }) },
      { label: '资料库…', click: () => openMain({ page: 'knowledge' }) },
      { label: '她记得的事…', click: () => openMain({ page: 'memory' }) },
      { type: 'separator' },
      { label: '今天做了什么', click: () => void desktop?.reportNow() },
      { label: '看看剪贴板里的报错', click: () => win?.webContents.send('clipboard:ask') },
      { label: '提醒…', click: () => openSettings('desktop') },
      { label: '免打扰（先藏起来）', accelerator: HIDE_SHORTCUT, click: () => toggleDnd(true) },
      { label: '设置…', click: () => openSettings('model') },
      { label: '回到右下角', click: resetPosition },
      { type: 'separator' },
      { label: '开发者工具', click: () => win?.webContents.openDevTools({ mode: 'detach' }) },
      { type: 'separator' },
      { label: '退出', click: () => app.quit() }
    ]).popup({ window: win })
  })

  ipcMain.handle('model:get-last', () => readModel(loadSettings().modelPath))
}

/** 托盘：桌面上的她被挡住、或者拖到别的屏幕上时，从这里也能打开主窗口 */
function createTray(): void {
  tray = new Tray(nativeImage.createFromBitmap(trayIcon(32), { width: 32, height: 32, scaleFactor: 2 }))
  tray.on('click', () => openMain({ page: 'tasks' }))
  updateTray()
}

/** 托盘菜单跟着免打扰的状态变 */
function updateTray(): void {
  if (!tray) return
  const dnd = desktop?.dnd ?? false
  tray.setToolTip(dnd ? '艾拉（免打扰中）' : '艾拉')
  tray.setContextMenu(
    Menu.buildFromTemplate([
      { label: '打开艾拉', click: () => openMain({ page: 'tasks' }) },
      { label: '和她说话', accelerator: CHAT_SHORTCUT, click: openChat },
      { label: '免打扰（藏起来）', type: 'checkbox', checked: dnd, accelerator: HIDE_SHORTCUT, click: () => toggleDnd() },
      { label: '让她回到右下角', click: resetPosition },
      { type: 'separator' },
      { label: '退出', click: () => app.quit() }
    ])
  )
}

// 只开一个：再启动一次（比如又双击了快捷方式）就把已经开着的主窗口拿到前面
const firstInstance = app.requestSingleInstanceLock()
if (!firstInstance) app.quit()
else app.on('second-instance', () => openMain({ page: 'tasks' }))

// 每个页面都只许显示艾拉自己的内容：不跳到别的网址、不开新窗口（见 navigation.ts）
app.on('web-contents-created', (_e, contents) =>
  lockDown(contents, process.env['ELECTRON_RENDERER_URL'], join(__dirname, '../renderer/index.html'))
)

app.whenReady().then(() => {
  if (!firstInstance) return
  // Windows 上系统通知（提醒）要有应用 ID；开发时用 electron.exe 的路径
  if (process.platform === 'win32') app.setAppUserModelId(app.isPackaged ? 'com.aila.desktop' : process.execPath)
  restrictPermissions()
  account = createAccount() // 加密存储要在 ready 之后才能用
  account.start()
  registerIpc()
  desktop = new DesktopKeeper({
    dataDir: app.getPath('userData'),
    character: () => win,
    characterEvent,
    onChange: (status) => {
      broadcast('desktop:status', status)
      updateTray()
    },
    loadPrefs: () => loadSettings().desktop,
    savePrefs: (prefs: DesktopPrefs) => saveSettings({ ...loadSettings(), desktop: prefs }),
    // 每天见面、日报的材料：编程任务（AI 服务）、她记得的事、聊天记录。AI 服务没开时这次先不说
    gather: async () => {
      const r = await agentRelay.list()
      if (!r.ok || !r.data) return null
      return { sessions: r.data, memories: account?.memories.list() ?? [], messages: account?.history.visible() ?? [] }
    },
    loadBriefing: () => loadSettings().briefing ?? {},
    saveBriefing: (state) => saveSettings({ ...loadSettings(), briefing: state }),
    // 她的一天：作品、日记在 AI 服务里，记着的状态在 settings.json
    life: {
      load: () => loadSettings().life ?? {},
      save: (state) => saveSettings({ ...loadSettings(), life: state }),
      works: async () => {
        const r = await studio.works(AI_URL)
        return r.ok && r.data ? r.data : null
      },
      start: (body) => studio.start(AI_URL, body),
      seen: async (id) => void (await studio.seen(AI_URL, id)),
      writeDiary: async (day, facts) => (await studio.writeDiary(AI_URL, day, facts)).ok
    }
  })
  void serviceKeeper.start()
  createWindow()
  createTray()
  desktop.start()
  if (!globalShortcut.register(CHAT_SHORTCUT, openChat)) {
    console.warn(`快捷键 ${CHAT_SHORTCUT} 被其他程序占用了，可以双击艾拉或者右键菜单打开对话`)
  }
  if (!globalShortcut.register(LISTEN_SHORTCUT, () => voiceMenu('listen'))) {
    console.warn(`快捷键 ${LISTEN_SHORTCUT} 被其他程序占用了，可以用右键菜单「听我说」`)
  }
  if (!globalShortcut.register(HIDE_SHORTCUT, () => toggleDnd())) {
    console.warn(`快捷键 ${HIDE_SHORTCUT} 被其他程序占用了，可以用托盘菜单的「免打扰」`)
  }
})

let serviceClosing = false
let serviceClosed = false
app.on('will-quit', (event) => {
  // Electron 不等待异步监听器；先拦住这次退出，最多等三秒结束自己开的服务，再继续退出。
  if (!serviceClosed) {
    event.preventDefault()
    if (!serviceClosing) {
      serviceClosing = true
      // 再退一次要等这次退出的事件处理完：服务不是我们开的（或者没找到）时 shutdown 立刻就好了，
      // 这时马上 app.quit() 会被 Electron 当成「已经在退出」忽略掉，窗口都关了进程却一直在
      void serviceKeeper.shutdown().finally(() => {
        serviceClosed = true
        setTimeout(() => app.quit(), 0)
      })
    }
    return
  }
  globalShortcut.unregisterAll()
  modelDownloader.stop()
  agentRelay.stopAll()
  account?.shutdown() // 还没写盘的聊天记录写下去
  desktop?.stop()
  tray?.destroy()
})
app.on('window-all-closed', () => app.quit())
