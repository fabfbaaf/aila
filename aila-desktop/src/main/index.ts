import { app, BrowserWindow, dialog, globalShortcut, ipcMain, Menu, screen, session } from 'electron'
import { join } from 'path'
import { existsSync, readFileSync, writeFileSync } from 'fs'
import { AI_SERVICE_URL, streamChat, type ChatMessage } from '../shared/chat'
import type { VoiceMenuAction, VoiceMenuState } from '../shared/voice'

// 桌面窗口：透明、无边框、置顶，只在角色身上响应鼠标
const WIN_WIDTH = 300
const WIN_HEIGHT = 480
const SNAP_DISTANCE = 24
/** 随时叫出对话框的全局快捷键（A = Aila） */
const CHAT_SHORTCUT = 'Alt+Shift+A'
/** 不用唤醒词，直接开始听（V = Voice） */
const LISTEN_SHORTCUT = 'Alt+Shift+V'
const AI_URL = process.env['AILA_AI_URL'] ?? AI_SERVICE_URL

let win: BrowserWindow | null = null
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

  if (process.env['ELECTRON_RENDERER_URL']) {
    // 开发时把渲染层的日志打印到终端，方便排查
    win.webContents.on('console-message', (_e, level, message) => {
      console.log(`[renderer:${level}] ${message}`)
    })
    win.loadURL(process.env['ELECTRON_RENDERER_URL'])
  } else {
    win.loadFile(join(__dirname, '../renderer/index.html'))
  }

  startCursorFeed()

  win.on('closed', () => {
    if (cursorTimer) clearInterval(cursorTimer)
    win = null
  })
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

async function chooseModel(): Promise<void> {
  if (!win) return
  const result = await dialog.showOpenDialog(win, {
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
    await streamChat(AI_URL, messages, controller.signal, (ev) => win?.webContents.send('chat:event', id, ev))
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
  ipcMain.handle('tts:synthesize', (_e, text: string) => synthesize(text))

  ipcMain.on('menu:show', (_e, voice: VoiceMenuState) => {
    if (!win) return
    Menu.buildFromTemplate([
      { label: '和她说话', accelerator: CHAT_SHORTCUT, click: openChat },
      { label: '听我说', accelerator: LISTEN_SHORTCUT, click: () => voiceMenu('listen') },
      { type: 'separator' },
      { label: '语音唤醒「你好艾拉」', type: 'checkbox', checked: voice.wake, click: () => voiceMenu('toggle-wake') },
      { label: '出声说话', type: 'checkbox', checked: voice.speak, click: () => voiceMenu('toggle-speak') },
      { type: 'separator' },
      { label: '加载 VRM 模型…', click: () => void chooseModel() },
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
  registerIpc()
  createWindow()
  if (!globalShortcut.register(CHAT_SHORTCUT, openChat)) {
    console.warn(`快捷键 ${CHAT_SHORTCUT} 被其他程序占用了，可以双击艾拉或者右键菜单打开对话`)
  }
  if (!globalShortcut.register(LISTEN_SHORTCUT, () => voiceMenu('listen'))) {
    console.warn(`快捷键 ${LISTEN_SHORTCUT} 被其他程序占用了，可以用右键菜单「听我说」`)
  }
})

app.on('will-quit', () => globalShortcut.unregisterAll())
app.on('window-all-closed', () => app.quit())
