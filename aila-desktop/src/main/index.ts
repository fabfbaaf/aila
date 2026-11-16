import {
  app,
  BrowserWindow,
  dialog,
  globalShortcut,
  ipcMain,
  Menu,
  safeStorage,
  screen,
  session,
  shell,
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
  deleteKnowledge,
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
import { AgentRelay } from './agent'
import type { VoiceMenuAction, VoiceMenuState } from '../shared/voice'
import { Account, type SecretBox } from './cloud/account'
import type { SocketLike } from './cloud/sync'

// 桌面窗口：透明、无边框、置顶，只在角色身上响应鼠标
const WIN_WIDTH = 300
const WIN_HEIGHT = 480
const SNAP_DISTANCE = 24
/** 随时叫出对话框的全局快捷键（A = Aila） */
const CHAT_SHORTCUT = 'Alt+Shift+A'
/** 不用唤醒词，直接开始听（V = Voice） */
const LISTEN_SHORTCUT = 'Alt+Shift+V'
const AI_URL = process.env['AILA_AI_URL'] ?? AI_SERVICE_URL

// 开发调试：设置 AILA_REMOTE_DEBUG=端口 时打开远程调试，用脚本驱动真实窗口做端到端测试（打包后无效）
if (process.env['AILA_REMOTE_DEBUG'] && !app.isPackaged) {
  app.commandLine.appendSwitch('remote-debugging-port', process.env['AILA_REMOTE_DEBUG'])
  app.commandLine.appendSwitch('remote-debugging-address', '127.0.0.1')
}

let win: BrowserWindow | null = null
let settingsWin: BrowserWindow | null = null
/** 魔法工作台（代码星图）窗口 */
let workbenchWin: BrowserWindow | null = null
/** 工作窗口（编程 Agent），和打开时要显示的会话 */
let agentWin: BrowserWindow | null = null
let agentFocus: string | null = null
const AGENT_MODES: AgentMode[] = ['read_only', 'ask', 'auto']
const agentRelay = new AgentRelay(AI_URL, {
  emit: (sid, e) => agentWin?.webContents.send('agent:event', sid, e),
  ended: (sid, error) => agentWin?.webContents.send('agent:stream-end', sid, error),
  progress: (progress) => {
    characterEvent({ kind: 'agent', progress })
    // 要你确认、窗口又不在前面：任务栏图标闪一闪（点开窗口就不闪了）
    if (progress.kind === 'confirm' && agentWin && !agentWin.isFocused()) agentWin.flashFrame(true)
  },
  // 她在哪个文件上干活：星图里读的星球闪一下、改过的变绿
  activity: (a) => workbenchWin?.webContents.send('workbench:activity', a),
  // 工作窗口里等你确认的改文件操作：星图里也显示这份 diff，双手合十可以确认
  confirm: (c) => {
    if (c) pendingFix = { id: c.id, detail: c.detail, source: 'agent' }
    else if (pendingFix?.source === 'agent') pendingFix = null
    else return
    workbenchWin?.webContents.send('workbench:pending-fix', pendingFix)
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
      sandbox: false
    }
  })

  // 默认鼠标穿透；渲染层发现鼠标在角色身上时再关闭穿透
  win.setIgnoreMouseEvents(true, { forward: true })
  win.setAlwaysOnTop(true, 'floating')

  win.once('ready-to-show', () => win?.showInactive())

  loadPage(win)
  startCursorFeed()

  win.on('closed', () => {
    if (cursorTimer) clearInterval(cursorTimer)
    win = null
  })
}

/** 角色窗口和设置窗口加载同一个页面，设置窗口的地址后面带 #/settings/分区 */
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

/** 设置窗口：普通窗口，可以调大小；已经开着就切到对应分区 */
function openSettings(section: SettingsSection = 'model'): void {
  if (settingsWin) {
    settingsWin.webContents.send('settings:section', section)
    if (settingsWin.isMinimized()) settingsWin.restore()
    settingsWin.show()
    settingsWin.focus()
    return
  }
  settingsWin = new BrowserWindow({
    width: 860,
    height: 600,
    minWidth: 700,
    minHeight: 480,
    title: '艾拉 · 设置',
    backgroundColor: '#f6f7fb',
    autoHideMenuBar: true,
    show: false,
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      contextIsolation: true,
      sandbox: false
    }
  })
  settingsWin.setMenu(null)
  settingsWin.once('ready-to-show', () => settingsWin?.show())
  settingsWin.on('closed', () => (settingsWin = null))
  loadPage(settingsWin, `/settings/${section}`)
}

/** 代码星图：大窗口、深色背景；已经开着就把新的报错交给它 */
function openWorkbench(focus?: WorkbenchFocus): void {
  if (workbenchWin) {
    if (focus) workbenchWin.webContents.send('workbench:focus', focus)
    if (workbenchWin.isMinimized()) workbenchWin.restore()
    workbenchWin.show()
    workbenchWin.focus()
    return
  }
  workbenchFocus = focus ?? null
  workbenchWin = new BrowserWindow({
    width: 1280,
    height: 820,
    minWidth: 900,
    minHeight: 600,
    title: '艾拉 · 代码星图',
    backgroundColor: '#04060d',
    autoHideMenuBar: true,
    show: false,
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      contextIsolation: true,
      sandbox: false
    }
  })
  workbenchWin.setMenu(null)
  workbenchWin.once('ready-to-show', () => workbenchWin?.show())
  workbenchWin.on('closed', () => (workbenchWin = null))
  loadPage(workbenchWin, '/workbench')
}

/** 工作窗口：在里面给她编程任务。已经开着就切到要看的会话 */
function openAgent(sid?: string): void {
  if (agentWin) {
    if (sid) agentWin.webContents.send('agent:focus', sid)
    if (agentWin.isMinimized()) agentWin.restore()
    agentWin.show()
    agentWin.focus()
    return
  }
  agentFocus = sid ?? null
  agentWin = new BrowserWindow({
    width: 1240,
    height: 820,
    minWidth: 860,
    minHeight: 560,
    title: '艾拉 · 编程',
    backgroundColor: '#0f1117',
    autoHideMenuBar: true,
    show: false,
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      contextIsolation: true,
      sandbox: false
    }
  })
  agentWin.setMenu(null)
  agentWin.once('ready-to-show', () => agentWin?.show())
  // 关了窗口不停任务：AI 服务那边照样做完，做完她会说一声（事件流还在主进程里收）
  agentWin.on('closed', () => (agentWin = null))
  loadPage(agentWin, '/agent')
}

function cleanFocus(raw: unknown): WorkbenchFocus | undefined {
  if (!raw || typeof raw !== 'object') return undefined
  const f = raw as Record<string, unknown>
  return {
    project: typeof f.project === 'string' ? f.project.slice(0, 64) : undefined,
    error: typeof f.error === 'string' ? f.error.slice(0, 8000) : undefined
  }
}

/** 状态变化发给所有窗口（角色窗口和设置窗口） */
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
    if (!win) return
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
  win.focus() // 窗口平时不抢焦点，要打字时才拿过来
  win.webContents.send('chat:open')
}

function voiceMenu(action: VoiceMenuAction): void {
  win?.webContents.send('voice:menu', action)
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
    onRemote: (changes) => win?.webContents.send('history:remote', changes),
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
  // 「好 / 不要」只认角色窗口发来的（设置窗口和别的页面不能替用户点头）
  ipcMain.handle('chat:confirm', (e, id: unknown, approved: unknown) => {
    if (e.sender !== win?.webContents || typeof id !== 'string' || id.length > 64 || typeof approved !== 'boolean') {
      return false
    }
    return answerConfirm(AI_URL, id, approved)
  })
  ipcMain.handle('tts:synthesize', (_e, text: string) => synthesize(text))

  ipcMain.handle('history:load', () => account?.history.visible() ?? [])
  ipcMain.on('history:append', (_e, m: unknown) => {
    if (isStoredMessage(m)) account?.append({ id: m.id, role: m.role, content: m.content, createdAt: m.createdAt })
  })
  ipcMain.on('settings:put', (_e, key: string, value: unknown) => account?.putSetting(key, value))
  ipcMain.handle('account:status', () => account?.status())
  ipcMain.handle('account:details', () => account?.details())
  ipcMain.handle('account:auth', (_e, form: AuthForm) =>
    account ? account.authenticate(form) : { ok: false, error: '还没准备好' }
  )
  ipcMain.handle('account:logout', () => account?.logout())

  ipcMain.handle('memory:list', () => account?.memories.list() ?? [])
  ipcMain.on('memory:delete', (_e, id: string) => {
    account?.deleteMemory(String(id))
    characterEvent({ kind: 'forget-one' })
  })
  ipcMain.handle('memory:forget-all', () => {
    const n = account?.forgetEverything() ?? 0
    if (n) characterEvent({ kind: 'forget-all' })
    return n
  })

  ipcMain.handle('knowledge:list', () => listKnowledge(AI_URL))
  ipcMain.handle('knowledge:delete', (_e, id: string) => deleteKnowledge(AI_URL, String(id)))
  ipcMain.handle('knowledge:add', async (e) => {
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
    const parent = BrowserWindow.fromWebContents(e.sender)
    if (!parent) return null
    const picked = await dialog.showOpenDialog(parent, {
      title: '选一个项目文件夹（代码只在这台电脑上建索引）',
      properties: ['openDirectory']
    })
    if (picked.canceled || picked.filePaths.length === 0) return null
    return addCodeProject(AI_URL, picked.filePaths[0])
  })
  ipcMain.handle('code:refresh', (_e, id: string) => codeProjectAction(AI_URL, String(id), 'refresh'))

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
  // 在星图里点「问她」「让她修」：只认星图窗口发来的，交给桌面上的她去说
  ipcMain.on('chat:say', (e, text: unknown) => {
    if (e.sender !== workbenchWin?.webContents || typeof text !== 'string' || !text.trim()) return
    win?.webContents.send('chat:say', text.slice(0, 8000))
  })
  // 工作窗口（编程 Agent）：只认工作窗口发来的
  const fromAgent = (e: { sender: Electron.WebContents }): boolean => !!agentWin && e.sender === agentWin.webContents
  const sidOk = (sid: unknown): sid is string => typeof sid === 'string' && /^[\w-]{1,64}$/.test(sid)
  const notAllowed = { ok: false, error: '只有工作窗口能用' }
  ipcMain.handle('agent:list', (e) => (fromAgent(e) ? agentRelay.list() : notAllowed))
  ipcMain.handle('agent:get', (e, sid: unknown) => (fromAgent(e) && sidOk(sid) ? agentRelay.get(sid) : notAllowed))
  ipcMain.handle('agent:create', (e, cwd: unknown, mode: unknown, isolate: unknown) => {
    if (!fromAgent(e) || typeof cwd !== 'string' || cwd.length > 1000) return notAllowed
    return agentRelay.create(cwd, AGENT_MODES.includes(mode as AgentMode) ? (mode as AgentMode) : 'ask', isolate === true)
  })
  ipcMain.handle('agent:merge', (e, sid: unknown, runTests: unknown) =>
    fromAgent(e) && sidOk(sid) ? agentRelay.merge(sid, runTests !== false) : notAllowed
  )
  ipcMain.handle('agent:discard', (e, sid: unknown) => (fromAgent(e) && sidOk(sid) ? agentRelay.discard(sid) : notAllowed))
  ipcMain.on('agent:send', (e, sid: unknown, text: unknown) => {
    if (fromAgent(e) && sidOk(sid) && typeof text === 'string' && text.trim()) agentRelay.send(sid, text.slice(0, 20000))
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
  ipcMain.handle('agent:confirm', (e, id: unknown, approved: unknown, remember: unknown) => {
    if (!fromAgent(e) || typeof id !== 'string' || id.length > 64 || typeof approved !== 'boolean') return false
    return agentRelay.confirm(id, approved, remember === true)
  })
  ipcMain.handle('agent:pick-folder', async (e) => {
    if (!fromAgent(e) || !agentWin) return null
    const res = await dialog.showOpenDialog(agentWin, { title: '选一个项目文件夹', properties: ['openDirectory'] })
    return res.canceled ? null : (res.filePaths[0] ?? null)
  })
  ipcMain.handle('agent:take-focus', (e) => {
    if (!fromAgent(e)) return null
    const sid = agentFocus
    agentFocus = null
    return sid
  })
  // 从别的窗口（菜单、设置、星图）打开工作窗口
  ipcMain.on('agent:open', (_e, sid: unknown) => openAgent(sidOk(sid) ? sid : undefined))
  // 角色窗口（屏幕上的报错「交给她修」）、星图（「让她修」）交过来的任务：开会话、开始做、打开工作窗口
  ipcMain.handle('agent:task', async (e, raw: unknown) => {
    const fromHere = e.sender === win?.webContents || e.sender === workbenchWin?.webContents
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
    workbenchWin?.webContents.send('workbench:pending-fix', pendingFix)
  })
  ipcMain.handle('workbench:pending-fix', () => pendingFix)
  // 星图里双手合十：只认星图窗口，而且只能确认它正显示着的那份修改。
  // 对话里的修改由角色窗口去回答「好」；工作窗口里的改文件操作直接回答 AI 服务（id 要对得上正在等的那个）
  ipcMain.handle('gesture:seal', async (e, id: unknown) => {
    if (e.sender !== workbenchWin?.webContents || !pendingFix || pendingFix.id !== id) return false
    if (pendingFix.source === 'agent') return agentRelay.approvePending(pendingFix.id)
    win?.webContents.send('gesture:approve', pendingFix.id)
    return true
  })
  ipcMain.handle('code:remove', (_e, id: string) => codeProjectAction(AI_URL, String(id), 'delete'))

  // 设置窗口
  ipcMain.on('settings:open', (_e, section?: SettingsSection) => openSettings(section))
  ipcMain.handle('ai:settings', () => getAiSettings(AI_URL))
  ipcMain.handle('ai:settings:put', async (_e, update: AiSettingsUpdate) => {
    const res = await putAiSettings(AI_URL, update)
    if (res.ok) characterEvent({ kind: 'settings-saved' })
    return res
  })
  ipcMain.handle('ai:settings:test', () => testLlm(AI_URL))
  ipcMain.handle('skills:list', () => listSkills(AI_URL, account?.skillOverrides() ?? {}))
  ipcMain.on('skills:set', (_e, id: string, on: boolean) => account?.setSkill(String(id), Boolean(on)))
  ipcMain.handle('model:path', () => loadSettings().modelPath ?? null)
  ipcMain.on('model:choose', (e) => void chooseModel(BrowserWindow.fromWebContents(e.sender)))
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
      { label: '编程任务…', click: () => openAgent() },
      { label: '代码星图…', click: () => openWorkbench() },
      { label: '资料库…', click: () => openSettings('knowledge') },
      { label: '她记得的事…', click: () => openSettings('memory') },
      { type: 'separator' },
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

app.whenReady().then(() => {
  restrictPermissions()
  account = createAccount() // 加密存储要在 ready 之后才能用
  account.start()
  registerIpc()
  createWindow()
  if (!globalShortcut.register(CHAT_SHORTCUT, openChat)) {
    console.warn(`快捷键 ${CHAT_SHORTCUT} 被其他程序占用了，可以双击艾拉或者右键菜单打开对话`)
  }
  if (!globalShortcut.register(LISTEN_SHORTCUT, () => voiceMenu('listen'))) {
    console.warn(`快捷键 ${LISTEN_SHORTCUT} 被其他程序占用了，可以用右键菜单「听我说」`)
  }
})

app.on('will-quit', () => {
  globalShortcut.unregisterAll()
  agentRelay.stopAll()
  account?.shutdown() // 还没写盘的聊天记录写下去
})
app.on('window-all-closed', () => app.quit())
