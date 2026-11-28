import {
  app,
  BrowserWindow,
  clipboard,
  dialog,
  globalShortcut,
  ipcMain,
  Menu,
  nativeImage,
  Notification,
  powerMonitor,
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
  codeArea,
  codeChain,
  codeDiagnose,
  codeEndpoints,
  codeExplain,
  codeFile,
  codeFunction,
  codeOutline,
  codeProjectAction,
  setCodeIgnore,
  deleteKnowledge,
  forgetEpisodes,
  listEpisodes,
  forgetAgentRule,
  getAgentRules,
  getAiSettings,
  getFixStats,
  getScreenStatus,
  listCodeProjects,
  listKnowledge,
  listSkills,
  putAiSettings,
  testLlm,
  brainModels,
  getPhone,
  setPhone,
  newPhonePairing,
  testPhonePush,
  forgetPhone,
  getAndroid,
  setAndroid,
  getAgendaToday,
  getAgendaUpcoming,
  testAgenda,
  listSchedules,
  addSchedule,
  changeSchedule,
  deleteSchedule,
  runSchedule,
  pairAndroid,
  connectAndroid
} from './ai'
import { dirname, join } from 'path'
import { existsSync, readFileSync, writeFileSync } from 'fs'
import WebSocket from 'ws'
import { AI_SERVICE_URL, answerConfirm, cleanLive, streamChat, type ChatMessage, type LiveReply } from '../shared/chat'
import { cleanMemoryEdit, type AuthForm, type MemoryOp, type StoredMessage } from '../shared/cloud'
import type { AiResult, AiSettingsUpdate, CharacterEvent, SettingsSection, VoicePrefs } from '../shared/settings'
import type { PendingFix, WorkbenchFocus } from '../shared/workbench'
import { isAgentEngine, type AgentMode, type PairAction } from '../shared/agent'
import { agentInstallUrl } from '../shared/agentInstall'
import { cleanRoute, routeForSection, toHash, type ShellRoute } from '../shared/shell'
import { TRAY_16, TRAY_32 } from './trayImage'
import { DesktopKeeper, type BriefingState } from './desktopKeeper'
import { discoveries } from './discoveries'
import { DISCOVERY_SOURCES, isDiscoveryId, isDiscoverySource } from '../shared/discoveries'
import { Drops } from './drops'
import type { DesktopPrefs } from '../shared/desktop'
import { AgentRelay } from './agent'
import { openLoginWindow } from './login'
import { lockDown } from './navigation'
import { validMcpName } from '../shared/mcp'
import { cleanSources, cleanUpdate, findAvatar, validExtensionId, validMotionName, validPageName, validStageToken } from '../shared/extensions'
import { droppedPathOk, openExtensionPage } from './extensions'
import { loadModelFile } from './modelFiles'
import { modelKind, type LoadedModel } from '../shared/model'
import { isLangPref, resolveLang, type LangPref } from '../shared/i18n'
import { mainLang, setMainLang, tm } from './lang'
import { RELEASES_API, dueForCheck, isReleaseUrl, parseRelease, type UpdateInfo } from '../shared/update'
import { SPOT_NAMES, SPOTS, isMoveTarget, targetPosition, walkDuration, walkPoint, type MoveTarget } from '../shared/move'
import { ENTRANCE_MS, cardBounds, emergePosition, homePosition, jumpHeight, jumpPoint, shouldEnter, type Point, type StagePhase } from '../shared/entrance'
import { presenterPosition, shouldIntro, type FxCommand } from '../shared/intro'
import { IntroDirector } from './intro'
import { registerAgentBudgetIpc } from './agentBudgetIpc'
import { registerAgentScreenshotIpc } from './agentScreenshotIpc'
import { registerAgentCreateIpc } from './agentCreateIpc'
import type { TaskWorkRequest } from '../shared/taskWork'
import { registerUnderstandIpc } from './understandIpc'
import type { VoiceMenuAction, VoiceMenuState } from '../shared/voice'
import { Account, type SecretBox } from './cloud/account'
import { forAi, type MemoryForAi } from './cloud/keeper'
import type { StoredMemory } from '../shared/cloud'
import type { SocketLike } from './cloud/sync'
import { createServiceKeeper, serviceLocator } from './serviceNode'
import { ModelDownloader } from './models'
import { registerServiceIpc } from './serviceIpc'
import { openWorkPreview, revealWork, studio, WORK_ID } from './studio'
import type { LifeState } from '../shared/studio'
import { createMainLog } from './mainLog'

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
// 开发调试：AILA_USER_DATA=文件夹 时数据放那里（打包后无效）。在真窗口里试东西不碰你自己的记忆、对话和设置，
// 也不和已经开着的艾拉抢「只开一个」的锁。要在下面任何 getPath('userData') 之前设
if (process.env['AILA_USER_DATA'] && !app.isPackaged) app.setPath('userData', process.env['AILA_USER_DATA'])
// 主进程的日志（装好的艾拉没有控制台）：没接住的异常记一行，照常按 Electron 默认的方式处理
const mainLog = createMainLog(app.getPath('userData'))
process.on('uncaughtExceptionMonitor', (err) => mainLog.write(`没接住的错误：${err?.stack ?? err}`))
process.on('unhandledRejection', (reason) => mainLog.write(`没接住的 Promise 错误：${reason instanceof Error ? reason.stack : String(reason)}`))
const AI_URL = process.env['AILA_AI_URL'] ?? AI_SERVICE_URL
const locateAi = serviceLocator({ packaged: app.isPackaged, appPath: app.getAppPath(), userData: app.getPath('userData') })
let servicePhase = ''
const serviceKeeper = createServiceKeeper({
  locate: locateAi,
  url: AI_URL,
  packaged: app.isPackaged,
  changed: (state) => {
    broadcast('service:state', state)
    const up = state.phase === 'ready' || state.phase === 'external'
    // AI 服务刚起来：把记忆推过去一次（它重启、换了数据目录也能对上）
    if (up && servicePhase !== 'ready' && servicePhase !== 'external') {
      if (account) void recoverThenPushFacts(account)
      pushLanguage()
    }
    servicePhase = state.phase
  }
})

/**
 * 关于你的记忆推给 AI 服务的记忆库（正本在这边，经云端同步）：记忆变了、服务刚起来时推一次，
 * 编程任务开头的「回想」用得上；删掉的那边也删。没推成也没关系，每次对话都会带着全部记忆再对齐一遍。
 * 一个接一个地推，后改的不会被先改的盖掉
 */
let factsPush: Promise<unknown> = Promise.resolve()

/**
 * AI 服务刚起来：先看它的记忆库里有没有本地丢了的记忆（以前存不进文件，重启就空了），取回来，再推过去。
 * 先推的话，空的列表会把那边留着的也删掉
 */
async function recoverThenPushFacts(acc: Account): Promise<void> {
  try {
    const res = await fetch(`${AI_URL}/memory/facts`, { signal: AbortSignal.timeout(10_000) })
    if (res.ok) {
      const n = acc.recoverMemories(((await res.json()) as { memories?: MemoryForAi[] }).memories ?? [])
      if (n) mainLog.write(`本地的记忆是空的，从 AI 服务的记忆库里取回了 ${n} 条`)
    }
  } catch {
    // 取不到就算了：照常推本地的
  }
  pushFacts(acc.memories.list())
}

function pushFacts(list: StoredMemory[]): void {
  const body = JSON.stringify({ memories: forAi(list) })
  factsPush = factsPush.then(() =>
    fetch(`${AI_URL}/memory/facts`, {
      method: 'PUT',
      headers: { 'content-type': 'application/json' },
      body,
      signal: AbortSignal.timeout(30_000)
    }).catch(() => undefined)
  )
}
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
const AGENT_MODES: AgentMode[] = ['read_only', 'ask', 'auto', 'full']
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
  /** 检查更新：上次什么时候查的、哪个新版本已经提醒过（同一版只提醒一次） */
  update?: { lastCheck?: string; notified?: string }
  /** 界面语言：auto 跟着系统 */
  language?: LangPref
  /** 开场演出演过了（第一次打开演一遍；设置里能再看一遍） */
  introDone?: boolean
}

// ---------- 界面语言（shared/i18n.ts） ----------

/** 现在用哪种语言（菜单、通知用；页面那边由 lang:changed 通知） */

function applyLanguage(): void {
  setMainLang(resolveLang(loadSettings().language, app.getLocale()))
}

/** 告诉 AI 服务（她用哪种语言回答）；服务没开也没关系，服务起来时会再推一次 */
function pushLanguage(): void {
  void putAiSettings(AI_URL, { language: mainLang() }).catch(() => undefined)
}

// ---------- 检查更新（shared/update.ts） ----------

let lastUpdate: UpdateInfo | null = null

/** 查 GitHub 上最新的发布。manual：你点了「检查更新」（自动检查有新版时只用系统通知提醒一次） */
async function checkForUpdate(manual: boolean): Promise<UpdateInfo> {
  const current = app.getVersion()
  try {
    const res = await fetch(RELEASES_API, {
      headers: { Accept: 'application/vnd.github+json', 'User-Agent': 'aila-desktop' },
      signal: AbortSignal.timeout(8000)
    })
    if (res.status === 404) {
      lastUpdate = { ...parseRelease({}, current), error: tm('还没有发布过版本。') }
    } else if (!res.ok) {
      throw new Error(`GitHub 返回 ${res.status}`)
    } else {
      lastUpdate = parseRelease(await res.json(), current)
    }
  } catch (err) {
    const why = err instanceof Error && err.name === 'TimeoutError' ? tm('连 GitHub 超时了') : tm('连不上 GitHub')
    return { ...parseRelease({}, current), error: tm('{why}，过会儿再试。', { why }) }
  }
  const s = loadSettings()
  const state = { ...s.update, lastCheck: lastUpdate.checkedAt }
  if (!manual && lastUpdate.newer && state.notified !== lastUpdate.latest && Notification.isSupported()) {
    state.notified = lastUpdate.latest
    const page = lastUpdate.page
    const n = new Notification({ title: tm('艾拉有新版本 {v}', { v: lastUpdate.latest }), body: tm('点这里打开下载页。在 设置 → 运行状态 里也能看。') })
    n.on('click', () => void shell.openExternal(page))
    n.show()
  }
  saveSettings({ ...loadSettings(), update: state })
  return lastUpdate
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

function readModel(path: string | undefined): LoadedModel | null {
  return loadModelFile(path)
}

// ---------- 窗口 ----------

function createWindow(): void {
  const { workArea } = screen.getPrimaryDisplay()
  const home = homePosition(workArea, { width: WIN_WIDTH, height: WIN_HEIGHT })
  // 第一次打开：开场演出（她站在主窗口左边当讲解员，shared/intro.ts）；以后打开：出场（她先站在屏幕中间的卡片里）。
  // 两种都是页面地址带 #enter，先不露面，等 character:entrance 再冒出来
  introPending = planIntro()
  const enter = introPending ? null : planEntrance()
  const size = { width: WIN_WIDTH, height: WIN_HEIGHT }
  const start = introPending ? presenterPosition(workArea, size) : enter ? emergePosition(enter.card, size) : home

  win = new BrowserWindow({
    width: WIN_WIDTH,
    height: WIN_HEIGHT,
    x: start.x,
    y: start.y,
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
    // 出场、开场演出时等她冒出来再露面（runEntrance、IntroDirector）
    if (!enter && !introPending && (desktop?.presence.visible ?? true)) win?.showInactive()
  })

  loadPage(win, enter || introPending ? 'enter' : '')
  startCursorFeed()
  if (enter) startEntrance(enter.card, home, workArea)

  win.on('closed', () => {
    if (cursorTimer) clearInterval(cursorTimer)
    win = null
  })
}

// ---------- 出场：卡片出来，她从里面跳到右下角打招呼（时间表和弧线在 shared/entrance.ts） ----------

interface Entrance {
  card: BrowserWindow | null
  home: Point
  workArea: Electron.Rectangle
  /** 页面准备好了（模型加载完）才开演 */
  ready: boolean
  started: boolean
  timer: ReturnType<typeof setTimeout> | null
  flight: ReturnType<typeof setInterval> | null
}

let entrance: Entrance | null = null
/** 第一次打开（还没见过面）：落地后说自我介绍，不说「早上好」 */
let firstMeet = false

function planEntrance(): { card: Electron.Rectangle } | null {
  const prefs = desktop?.prefs
  if (!prefs) return null
  const ok = shouldEnter({ entrance: prefs.entrance, showCharacter: prefs.showCharacter, visible: desktop?.presence.visible ?? true, dnd: desktop?.dnd ?? false })
  return ok ? { card: cardBounds(screen.getPrimaryDisplay().workArea) } : null
}

function startEntrance(cardRect: Electron.Rectangle, home: Point, workArea: Electron.Rectangle): void {
  const card = new BrowserWindow({
    ...cardRect,
    transparent: true,
    backgroundColor: '#00000000',
    frame: false,
    resizable: false,
    movable: false,
    hasShadow: false,
    alwaysOnTop: true,
    skipTaskbar: true,
    focusable: false,
    show: false,
    webPreferences: { contextIsolation: true, sandbox: true } // 卡片只是画面，不给预加载的接口
  })
  card.setIgnoreMouseEvents(true) // 点不到它：不挡你点后面的东西
  card.once('ready-to-show', () => card.showInactive())
  card.on('closed', () => {
    if (entrance) entrance.card = null
  })
  loadPage(card, 'launch')
  entrance = { card, home, workArea, ready: false, started: false, timer: null, flight: null }
  // 页面一直没准备好（模型坏了、卡住）：不演了，直接站到右下角
  entrance.timer = setTimeout(() => finishEntrance(true), ENTRANCE_MS.timeout)
}

/** 角色页面加载完模型后来报到：在出场就开演，返回 true（页面先别打招呼，落地时再说） */
function entranceReady(): boolean {
  if (introPending && intro) {
    introPending = false
    void intro.start()
    return true
  }
  if (!entrance || entrance.started) return false
  entrance.ready = true
  entrance.started = true
  void runEntrance(entrance)
  return true
}

/** quiet：落地时不自己打招呼（开场演出里台词由导演给） */
function sendEntrance(phase: StagePhase, quiet = false): void {
  win?.webContents.send('character:entrance', { phase, first: firstMeet, quiet })
}

// ---------- 开场演出（剧本在 shared/intro.ts，导演在 main/intro.ts） ----------

let intro: IntroDirector | null = null
/** 这次启动要演（角色页面报到时开演） */
let introPending = false

function planIntro(): boolean {
  const prefs = desktop?.prefs
  if (!prefs) return false
  return shouldIntro({ introDone: !!loadSettings().introDone, showCharacter: prefs.showCharacter, visible: desktop?.presence.visible ?? true })
}

/** 主窗口打开到这个位置，等页面画好（开场演出用） */
async function openMainAt(bounds: Electron.Rectangle): Promise<BrowserWindow | null> {
  const existed = !!mainWin
  openMain({ page: 'tasks' })
  const w = mainWin as BrowserWindow | null
  if (!w) return null
  w.setBounds(bounds)
  if (!existed) {
    await new Promise<void>((r) => {
      if (!w.webContents.isLoading()) return r()
      w.webContents.once('did-finish-load', () => r())
    })
    await new Promise((r) => setTimeout(r, 700)) // 等页面挂好
  }
  w.show()
  return w
}

/** 她走到某个位置（带走路动作），走到了返回；被拖住、被藏起来也返回 */
function walkToPoint(to: Point): Promise<void> {
  return new Promise((resolve) => {
    if (!win || !win.isVisible()) return resolve()
    stopWalk()
    const b = win.getBounds()
    const from = { x: b.x, y: b.y }
    const ms = walkDuration(from, to) * 2.2 // 演出里慢慢走过去（和自己闲逛一样慢），不是「叫她去」那种小跑
    const start = Date.now()
    win.webContents.send('character:walking', Math.sign(to.x - from.x))
    walking = setInterval(() => {
      if (!win || win.isDestroyed() || !win.isVisible() || drag) {
        stopWalk()
        return resolve()
      }
      const t = (Date.now() - start) / ms
      const p = walkPoint(from, to, t)
      win.setPosition(p.x, p.y)
      if (t >= 1) {
        stopWalk()
        win.webContents.send('character:arrived', 'stroll')
        resolve()
      }
    }, 16)
  })
}

/** 开场演出的全屏特效窗口（shell/FxLayer.vue）：铺满工作区、透明、点不到；没有预加载接口，指令直接调页面里的 window.__fx */
let fxWin: BrowserWindow | null = null

function openFx(): Promise<void> {
  if (fxWin && !fxWin.isDestroyed()) return Promise.resolve()
  const wa = screen.getPrimaryDisplay().workArea
  const w = new BrowserWindow({
    ...wa,
    transparent: true,
    backgroundColor: '#00000000',
    frame: false,
    resizable: false,
    movable: false,
    hasShadow: false,
    alwaysOnTop: true,
    skipTaskbar: true,
    focusable: false,
    show: false,
    webPreferences: { contextIsolation: true, sandbox: true }
  })
  w.setIgnoreMouseEvents(true)
  w.setAlwaysOnTop(true, 'floating')
  fxWin = w
  w.on('closed', () => {
    if (fxWin === w) fxWin = null
  })
  loadPage(w, 'fx')
  return new Promise((resolve) => {
    w.once('ready-to-show', async () => {
      w.showInactive()
      // 等页面里的 window.__fx 挂好再说开好了，不然紧接着发的指令（比如盖上玻璃）会丢
      for (let i = 0; i < 40 && !w.isDestroyed(); i++) {
        if (await w.webContents.executeJavaScript('!!window.__fx').catch(() => false)) break
        await new Promise((r) => setTimeout(r, 50))
      }
      resolve()
    })
  })
}

function sendFx(c: FxCommand): void {
  if (!fxWin || fxWin.isDestroyed()) return
  void fxWin.webContents.executeJavaScript(`window.__fx && window.__fx(${JSON.stringify(c)})`).catch(() => undefined)
}

/** 特效窗口压在她上面（玻璃在她前面）还是她在最上面 */
function fxAbove(on: boolean): void {
  if (on) fxWin?.moveTop()
  else win?.moveTop()
}

function closeFx(): void {
  if (fxWin && !fxWin.isDestroyed()) fxWin.destroy()
  fxWin = null
}

function createIntro(): IntroDirector {
  let escape: (() => void) | null = null
  return new IntroDirector({
    pet: () => win,
    main: () => mainWin,
    openMain: openMainAt,
    petSize: { width: WIN_WIDTH, height: WIN_HEIGHT },
    workArea: () => screen.getPrimaryDisplay().workArea,
    home: () => homePosition(screen.getPrimaryDisplay().workArea, { width: WIN_WIDTH, height: WIN_HEIGHT }),
    cue: (c) => win?.webContents.send('intro:cue', c),
    scene: (s) => mainWin?.webContents.send('intro:scene', s),
    phase: (p, quiet = true) => sendEntrance(p, quiet),
    go: (route) => mainWin?.webContents.send('shell:go', route),
    walk: walkToPoint,
    dragging: () => !!drag,
    // 借鼠标那几秒按 Esc 收回：只在这时占着 Esc，借完马上放掉
    holdEscape: (on, cb) => {
      if (on && !escape) {
        escape = cb
        globalShortcut.register('Escape', () => escape?.())
      } else if (!on && escape) {
        globalShortcut.unregister('Escape')
        escape = null
      }
    },
    markDone: () => saveSettings({ ...loadSettings(), introDone: true }),
    fx: { open: openFx, send: sendFx, close: closeFx, above: fxAbove }
  })
}

async function runEntrance(e: Entrance): Promise<void> {
  const sleep = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms))
  await sleep(ENTRANCE_MS.card)
  if (entrance !== e || !win || win.isDestroyed()) return
  if (desktop?.presence.visible ?? true) {
    win.showInactive()
    win.moveTop() // 在卡片上面
  }
  sendEntrance('emerge')
  await sleep(ENTRANCE_MS.emerge)
  if (entrance !== e || !win) return
  sendEntrance('crouch')
  await sleep(ENTRANCE_MS.crouch)
  if (entrance !== e || !win) return
  sendEntrance('jump')
  fadeCard(e)
  const [x, y] = win.getPosition()
  const from = { x, y }
  // 她站在窗口下半部分，上面 40% 是透明的：这截可以跳到屏幕外面，头不会出去
  const height = jumpHeight(from, e.home, e.workArea, Math.round(WIN_HEIGHT * 0.4))
  const begin = Date.now()
  e.flight = setInterval(() => {
    if (!win || win.isDestroyed() || drag) return finishEntrance(false) // 半空中被你拖住了：就停在那儿
    const t = (Date.now() - begin) / ENTRANCE_MS.jump
    const p = jumpPoint(from, e.home, t, height)
    win.setPosition(p.x, p.y)
    if (t >= 1) finishEntrance(false)
  }, 16)
}

/** 卡片慢慢变透明再关掉 */
function fadeCard(e: Entrance): void {
  const card = e.card
  if (!card || card.isDestroyed()) return
  const begin = Date.now()
  const fade = setInterval(() => {
    if (card.isDestroyed()) return clearInterval(fade)
    const t = (Date.now() - begin) / ENTRANCE_MS.fade
    card.setOpacity(Math.max(0, 1 - t))
    if (t >= 1) {
      clearInterval(fade)
      card.destroy()
    }
  }, 16)
}

/** 出场结束：落地打招呼；skipped（超时没演成）时直接站到右下角 */
function finishEntrance(skipped: boolean): void {
  const e = entrance
  if (!e) return
  entrance = null
  if (e.timer) clearTimeout(e.timer)
  if (e.flight) clearInterval(e.flight)
  if (e.card && !e.card.isDestroyed()) e.card.destroy()
  if (!win || win.isDestroyed()) return
  if (skipped) {
    win.setPosition(e.home.x, e.home.y)
    if (desktop?.presence.visible ?? true) win.showInactive()
  }
  sendEntrance('land')
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
    title: tm('艾拉'),
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
    title: tm('选择形象：VRM 模型，或者 Live2D 模型的 .model3.json'),
    filters: [
      { name: tm('VRM 或 Live2D 模型'), extensions: ['vrm', 'json'] },
      { name: tm('VRM 模型'), extensions: ['vrm'] },
      { name: tm('Live2D 模型（.model3.json）'), extensions: ['json'] }
    ],
    properties: ['openFile']
  })
  if (result.canceled || result.filePaths.length === 0) return
  const path = result.filePaths[0]
  if (!modelKind(path)) {
    await dialog.showMessageBox(parent, { type: 'info', message: tm('要选 .vrm 文件，或者 Live2D 模型文件夹里的 .model3.json。') })
    return
  }
  const model = loadModelFile(path)
  if (!model) {
    await dialog.showMessageBox(parent, { type: 'info', message: tm('这个模型读不了（文件太多、太大，或者打不开）。') })
    return
  }
  saveSettings({ ...loadSettings(), modelPath: path })
  win.webContents.send('model:loaded', model)
  broadcast('model:path', path)
}

// ---------- 她自己走到屏幕上的某个位置（「去右上角」「往左一点」，算法在 shared/move.ts） ----------

let walking: ReturnType<typeof setInterval> | null = null

function stopWalk(): void {
  if (walking) clearInterval(walking)
  walking = null
}

/** 在她现在所在的那块屏幕上走过去；你拖她、她被藏起来都会停下 */
function walkTo(target: MoveTarget): void {
  if (!win || !win.isVisible()) return
  if (target === 'stroll') {
    // 自己闲逛：你正在打字、拖着她、她正在走，或者你离开好一阵了（没人看），都不走
    const idle = powerMonitor.getSystemIdleTime()
    if (drag || walking || idle < 3 || idle > 600) return
  }
  stopWalk()
  const b = win.getBounds()
  const from = { x: b.x, y: b.y }
  const to = targetPosition(target, b, screen.getDisplayMatching(b).workArea)
  if (from.x === to.x && from.y === to.y) {
    win.webContents.send('character:arrived', target)
    return
  }
  // 闲逛慢慢走，叫她去哪就快点
  const ms = walkDuration(from, to) * (target === 'stroll' ? 2.2 : 1)
  const start = Date.now()
  win.webContents.send('character:walking', Math.sign(to.x - from.x))
  walking = setInterval(() => {
    if (!win || win.isDestroyed() || !win.isVisible()) return stopWalk()
    const t = (Date.now() - start) / ms
    const p = walkPoint(from, to, t)
    win.setPosition(p.x, p.y)
    if (t >= 1) {
      stopWalk()
      win.webContents.send('character:arrived', target)
    }
  }, 16)
}

function resetPosition(): void {
  if (!win) return
  const { workArea } = screen.getPrimaryDisplay()
  win.setPosition(workArea.x + workArea.width - WIN_WIDTH - 40, workArea.y + workArea.height - WIN_HEIGHT)
}

function openChat(): void {
  if (!win) return
  desktop?.setDnd(false)
  // 设置里关了「桌面上显示她」：临时叫出来，对话框关了（chat:closed）再藏回去
  if (desktop && !desktop.prefs.showCharacter) desktop.summon(true)
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

/** 桌面上显示 / 不显示她（托盘、设置页都走偏好；关掉时打开主窗口，免得整个应用像是消失了） */
function setShowCharacter(on: boolean): void {
  desktop?.setPrefs({ ...desktop.prefs, showCharacter: on })
  updateTray()
  if (!on) openMain({ page: 'tasks' })
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
    onSaveProblem: (problem, file) =>
      mainLog.write(problem ? `存不进文件 ${file}：${problem.error}（过一会儿再试）` : `${file} 又存上了`),
    box: secretBox(),
    openSocket: (url, token) =>
      new WebSocket(url, { headers: { Authorization: `Bearer ${token}` } }) as unknown as SocketLike,
    onStatus: (status) => broadcast('account:status', status),
    onRemote: (changes) => {
      win?.webContents.send('history:remote', changes)
      mainWin?.webContents.send('history:changed')
    },
    onMemories: (list) => {
      broadcast('memory:changed', list)
      pushFacts(list)
    },
    extractMemories: async (req) => {
      const res = await fetch(`${AI_URL}/memory/extract`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ ...req, learnTopics: desktop?.prefs.proactiveSharing === true }),
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
    // 还有认识多久了：她按这个决定拘谨还是放松
    const extra = { memories: account?.memoriesForChat() ?? [], skills: account?.skillOverrides() ?? {}, bond: account?.bond() }
    await streamChat(
      AI_URL,
      messages,
      controller.signal,
      (ev) => {
        // 她在对话里把编程任务交给了工作窗口：打开工作窗口、接上那个任务
        if (ev.type === 'agent_task') openAgent(ev.session)
        // 她带你去某一页（「这个设置在哪」）：地址只认 shell.ts 里的页面和分区
        if (ev.type === 'open_page') openMain(cleanRoute(ev.route))
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
    stopWalk() // 走到一半被你拎起来了
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
  // 对话框关了：临时叫出来的她藏回去（只认角色窗口）
  ipcMain.on('chat:closed', (e) => {
    if (win && e.sender === win.webContents) desktop?.summon(false)
  })

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
  ipcMain.handle('bond:get', () => account?.bond() ?? null)
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
    !fromMainWindow(e) ? MAIN_ONLY : account ? account.authenticate(form) : { ok: false, error: tm('还没准备好') }
  )
  ipcMain.handle('account:logout', (e) => (fromMainWindow(e) ? account?.logout() : undefined))

  ipcMain.handle('memory:list', () => account?.memories.list() ?? [])
  ipcMain.on('memory:delete', (e, id: string) => {
    if (!fromMainWindow(e)) return
    account?.deleteMemory(String(id))
    characterEvent({ kind: 'forget-one' })
  })
  // 检查更新、打开下载页：只认主窗口；打开的地址只能是这个仓库在 GitHub 上的
  ipcMain.handle('update:check', (e) => (fromMainWindow(e) ? checkForUpdate(true) : null))
  ipcMain.handle('update:last', (e) => (fromMainWindow(e) ? (lastUpdate ?? { ...parseRelease({}, app.getVersion()), checkedAt: '' }) : null))
  ipcMain.on('update:open', (e, url: unknown) => {
    if (fromMainWindow(e) && isReleaseUrl(url)) void shell.openExternal(url)
  })
  // 你自己加、改一条记忆：只认主窗口，内容检查过（shared/cloud.ts）
  ipcMain.handle('memory:edit', (e, raw: unknown) => {
    if (!fromMainWindow(e)) return MAIN_ONLY
    const op = cleanMemoryEdit(raw)
    if (typeof op === 'string') return { ok: false, error: op }
    if (!account?.editMemory(op)) return { ok: false, error: tm('这条记忆已经不在了（可能在别的设备上删了）。') }
    return { ok: true }
  })
  // 经历（对话摘要、编程任务做了什么）：只在本机的 AI 服务里，只给主窗口看、只让主窗口删
  ipcMain.handle('memory:episodes', (e) => (fromMainWindow(e) ? listEpisodes(AI_URL) : []))
  ipcMain.handle('memory:forget-episode', (e, id: unknown) =>
    fromMainWindow(e) ? forgetEpisodes(AI_URL, typeof id === 'string' && id ? id.slice(0, 64) : undefined) : false
  )
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
      title: tm('添加资料（只保存在这台电脑上）'),
      filters: [{ name: tm('文档'), extensions: ['txt', 'md', 'markdown', 'pdf', 'docx', 'html', 'htm'] }],
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
      title: tm('选一个项目文件夹（代码只在这台电脑上建索引）'),
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
  // 在地图里点「问她」、在主窗口的对话页打字：只认主窗口发来的，交给桌面上的她去说
  ipcMain.on('chat:say', (e, text: unknown) => {
    if (e.sender !== mainWin?.webContents || typeof text !== 'string' || !text.trim()) return
    win?.webContents.send('chat:say', text.slice(0, 8000))
  })
  // 她正在说的回复：只认角色窗口发来的，检查过再转给主窗口；主窗口打开对话页时先取一次现在的状态
  let liveReply: LiveReply | null = null
  ipcMain.on('chat:live', (e, raw: unknown) => {
    if (!win || e.sender !== win.webContents) return
    const r = cleanLive(raw)
    if (!r) return
    liveReply = r
    mainWin?.webContents.send('chat:live', r)
  })
  ipcMain.handle('chat:live-state', (e) => (e.sender === mainWin?.webContents ? liveReply : null))
  // 主窗口里回答她的确认、让她停下：只认主窗口，交给角色窗口（确认要对得上她正在问的那一个）
  ipcMain.on('chat:answer', (e, id: unknown, approved: unknown) => {
    if (e.sender !== mainWin?.webContents || typeof id !== 'string' || typeof approved !== 'boolean') return
    win?.webContents.send('chat:answer', id.slice(0, 64), approved)
  })
  ipcMain.on('chat:stop', (e) => {
    if (e.sender === mainWin?.webContents) win?.webContents.send('chat:stop')
  })
  // 编程 Agent：只认主窗口发来的（角色窗口不能替你开会话、点「可以」）
  const fromAgent = (e: { sender: Electron.WebContents }): boolean => !!mainWin && e.sender === mainWin.webContents
  registerServiceIpc(ipcMain, serviceKeeper, fromAgent)
  ipcMain.handle('models:status', (e) => fromAgent(e) ? modelDownloader.status() : null)
  ipcMain.handle('models:download', (e) => fromAgent(e) ? modelDownloader.start() : { ok: false, error: tm('只有主窗口能用') })
  const sidOk = (sid: unknown): sid is string => typeof sid === 'string' && /^[\w-]{1,64}$/.test(sid)
  const notAllowed = { ok: false, error: tm('只有主窗口能用') }
  ipcMain.handle('agent:list', (e, q: unknown) =>
    fromAgent(e) ? agentRelay.list(typeof q === 'string' ? q.slice(0, 200) : '') : notAllowed
  )
  ipcMain.handle('agent:get', (e, sid: unknown) => (fromAgent(e) && sidOk(sid) ? agentRelay.get(sid) : notAllowed))
  registerAgentCreateIpc(ipcMain, agentRelay, fromAgent)
  ipcMain.handle('agent:taskwork', (e, request: TaskWorkRequest | null) =>
    fromAgent(e) && request && typeof request === 'object' && typeof request.action === 'string'
      && JSON.stringify(request).length <= 60000 ? agentRelay.taskWork(request) : notAllowed)
  // 看懂项目：整体架构、模块、链路、函数，AI 讲解（花钱，只认主窗口）
  registerUnderstandIpc(ipcMain, {
    area: (id, path, tests) => codeArea(AI_URL, id, path, tests),
    chain: (id, file, endpoint) => codeChain(AI_URL, id, file, endpoint),
    endpoints: (id) => codeEndpoints(AI_URL, id),
    file: (id, file) => codeFile(AI_URL, id, file),
    fn: (id, file, line) => codeFunction(AI_URL, id, file, line),
    explain: (id, target) => codeExplain(AI_URL, id, target),
    diagnose: (id, text, depth) => codeDiagnose(AI_URL, id, text, depth)
  }, fromMainWindow)
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
      return { ok: false, error: tm('标题要有 1–100 个字。') }
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
    if (typeof text !== 'string' || !text.trim() || text.length > 2000) return { ok: false, error: tm('要记的话不能是空的，也别太长。') }
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
  // 本机的 Codex、Claude Code：看情况、装适配器（装适配器要下载，只认主窗口）
  ipcMain.handle('agent:local', (e) => (fromAgent(e) ? agentRelay.localAgents() : notAllowed))
  ipcMain.handle('agent:quotas', (e, refresh: unknown) => (
    fromAgent(e) ? agentRelay.providerQuotas(refresh === true) : notAllowed
  ))
  ipcMain.handle('agent:quotas:record', (e, raw: unknown) => {
    if (!fromAgent(e) || !raw || typeof raw !== 'object') return notAllowed
    const body = raw as import('../shared/providerQuotas').QuotaRecord
    const fields = [body.provider, body.name, body.window, body.label, body.unit]
    if (!fields.every((field) => field === undefined || (typeof field === 'string' && field.length <= 60))) return notAllowed
    if (![body.remaining, body.total, body.reset_at].every((n) => n === null || Number.isFinite(n))) return notAllowed
    const { provider, name, window, label, unit, remaining, total, reset_at } = body
    return agentRelay.recordProviderQuota({ provider, name, window, label, unit, remaining, total, reset_at })
  })
  ipcMain.handle('agent:quotas:claude', (e) => fromAgent(e) ? agentRelay.claudeQuotaConfig() : notAllowed)
  ipcMain.handle('agent:quotas:cursor', (e, linked: unknown) => (
    fromAgent(e) && typeof linked === 'boolean' ? agentRelay.linkCursorQuota(linked) : notAllowed
  ))
  ipcMain.handle('agent:local:install', (e) => (fromAgent(e) ? agentRelay.installAdapters() : notAllowed))
  ipcMain.handle('agent:local:login', async (e, engine: unknown) => {
    if (!fromAgent(e) || !isAgentEngine(engine)) return notAllowed
    const result = await agentRelay.loginCommand(engine)
    return result.ok ? openLoginWindow(result.data?.command, undefined, undefined, result.data?.profile) : result
  })
  ipcMain.handle('agent:local:open-install', async (e, id: unknown) => {
    if (!fromAgent(e)) return notAllowed
    const url = agentInstallUrl(id)
    if (!url) return { ok: false, error: tm('不认识的 Agent。') }
    try {
      await shell.openExternal(url)
      return { ok: true }
    } catch {
      return { ok: false, error: tm('没打开官方安装页。') }
    }
  })
  ipcMain.handle('agent:models', (e, use: unknown, refresh: unknown) =>
    fromAgent(e) && (use === 'chat' || use === 'task') ? agentRelay.models(use, refresh === true) : notAllowed)
  ipcMain.handle('agent:handoff', (e, sid: unknown, engine: unknown, model: unknown, label: unknown) =>
    fromAgent(e) && sidOk(sid) && (engine === null || isAgentEngine(engine)) && typeof model === 'string' && model.length <= 200
      && typeof label === 'string' && label.length <= 120 ? agentRelay.handoff(sid, engine, model, label) : notAllowed)
  ipcMain.handle('agent:pair', (e, sid: unknown, payload: Partial<PairAction> | null) => {
    if (!fromAgent(e) || !sidOk(sid) || !payload || typeof payload !== 'object') return notAllowed
    if (payload.action === 'validate') return agentRelay.pair(sid, { action: 'validate' })
    if (payload.action === 'close') return agentRelay.pair(sid, { action: 'close' })
    if (!('proposal_id' in payload) || typeof payload.proposal_id !== 'string' ||
      !/^[\w-]{1,64}$/.test(payload.proposal_id)) return notAllowed
    if (payload.action === 'reject') return agentRelay.pair(sid, { action: 'reject', proposal_id: payload.proposal_id })
    if (payload.action !== 'approve' || !Array.isArray(payload.picks) || payload.picks.length !== 2) return notAllowed
    if (payload.picks.some((pick) => !pick || !isAgentEngine(pick.agent) || typeof pick.model !== 'string' || pick.model.length > 200)) return notAllowed
    const picks = payload.picks.map(({ agent, model }) => ({ agent, model }))
    return agentRelay.pair(sid, { action: 'approve', proposal_id: payload.proposal_id, picks })
  })
  // 本机 Agent 任务的模型、思考深度：只认主窗口，名字和值的长度先挡一下，合不合法 AI 服务按它给的选项再查
  const optionText = (v: unknown, max: number): v is string => typeof v === 'string' && v.length > 0 && v.length <= max
  ipcMain.handle('agent:options', (e, sid: unknown) => (fromAgent(e) && sidOk(sid) ? agentRelay.engineOptions(sid) : notAllowed))
  ipcMain.handle('agent:options:set', (e, sid: unknown, id: unknown, value: unknown) =>
    fromAgent(e) && sidOk(sid) && optionText(id, 80) && optionText(value, 200) ? agentRelay.setEngineOption(sid, id, value) : notAllowed)
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
  // 扩展包：装扩展等于让这台电脑多跑程序、给她新的做事说明，只认主窗口。
  // 包由主进程弹窗选（渲染层不能指定任意路径），选好先检查，渲染层拿到清单给你看，你点「装」才装
  ipcMain.handle('ext:list', (e) => (fromAgent(e) ? agentRelay.extList() : notAllowed))
  ipcMain.handle('ext:pick', async (e, kind: unknown) => {
    if (!fromAgent(e) || (kind !== 'zip' && kind !== 'folder')) return notAllowed
    const parent = BrowserWindow.fromWebContents(e.sender)
    if (!parent) return notAllowed
    const picked = await dialog.showOpenDialog(parent, kind === 'zip'
      ? { title: tm('选一个扩展包（.zip）'), filters: [{ name: tm('扩展包'), extensions: ['zip'] }], properties: ['openFile'] }
      : { title: tm('选一个扩展文件夹（里面有 aila-extension.json）'), properties: ['openDirectory'] })
    if (picked.canceled || picked.filePaths.length === 0) return { ok: true, data: null }
    return agentRelay.extInspect(picked.filePaths[0])
  })
  // 装了、改了、卸了以后告诉桌面上的她重新拿动作和台词
  const extChanged = <T,>(res: AiResult<T>): AiResult<T> => {
    if (res.ok) win?.webContents.send('ext:changed')
    return res
  }
  // 拖进设置页的 zip 或文件夹：预加载脚本从拖放的文件里取的路径，这里再核对是个存在的文件夹或 .zip
  ipcMain.handle('ext:dropped', (e, path: unknown) => {
    if (!fromAgent(e)) return notAllowed
    if (!droppedPathOk(path)) return { ok: false, error: tm('拖进来的要是一个文件夹或者 .zip') }
    return agentRelay.extInspect(path)
  })
  ipcMain.handle('ext:install', async (e, token: unknown) =>
    fromAgent(e) && validStageToken(token) ? extChanged(await agentRelay.extInstall(token)) : notAllowed
  )
  ipcMain.handle('ext:discard', (e, token: unknown) => (fromAgent(e) && validStageToken(token) ? agentRelay.extDiscard(token) : notAllowed))
  ipcMain.handle('ext:update', async (e, id: unknown, raw: unknown) => {
    const update = cleanUpdate(raw)
    return fromAgent(e) && validExtensionId(id) && update ? extChanged(await agentRelay.extUpdate(id, update)) : notAllowed
  })
  ipcMain.handle('ext:remove', async (e, id: unknown) =>
    fromAgent(e) && validExtensionId(id) ? extChanged(await agentRelay.extRemove(id)) : notAllowed
  )
  // 第二期：桌面伙伴。动作和台词不是机密，两个窗口都能读（角色窗口要用）
  ipcMain.handle('ext:companion', () => agentRelay.extCompanion())
  // 扩展页里点「试一下」：让桌面上的她做这个动作（动作数据由她自己那边从扩展列表拿，这里只传名字）
  ipcMain.on('ext:play', (e, name: unknown) => {
    if (fromAgent(e) && validMotionName(name)) win?.webContents.send('ext:play', name)
  })
  // 换成扩展带的形象：路径从 AI 服务的列表里取，再确认在扩展文件夹的 avatars 里
  ipcMain.handle('ext:use-avatar', async (e, id: unknown, name: unknown) => {
    if (!fromAgent(e) || !validExtensionId(id) || !validPageName(name)) return notAllowed
    const res = await agentRelay.extCompanion()
    const file = findAvatar(res.data, id, name)
    if (!file) return { ok: false, error: tm('没找到这个形象（扩展可能停用或者卸载了）') }
    const data = readModel(file)
    if (!data || !win) return { ok: false, error: tm('形象文件读不了') }
    saveSettings({ ...loadSettings(), modelPath: file })
    win.webContents.send('model:loaded', data)
    broadcast('model:path', file)
    return { ok: true, data: { ok: true } }
  })
  // 第三期：扩展自带的页面，开在沙盒窗口里
  ipcMain.handle('ext:open-page', (e, id: unknown, name: unknown, title: unknown) =>
    fromAgent(e) && validExtensionId(id) && validPageName(name)
      ? openExtensionPage(AI_URL, id, name, typeof title === 'string' ? title.slice(0, 40) : name, mainWin)
      : notAllowed
  )
  // 第三期：目录（联网下载在 AI 服务那边做，只许 https 公网地址、要对上 sha256）
  ipcMain.handle('ext:catalog', (e) => (fromAgent(e) ? agentRelay.extCatalog() : notAllowed))
  ipcMain.handle('ext:catalog-sources', (e, raw: unknown) => {
    const urls = cleanSources(raw)
    return fromAgent(e) && urls ? agentRelay.extCatalogSources(urls) : notAllowed
  })
  ipcMain.handle('ext:catalog-inspect', (e, catalog: unknown, id: unknown) =>
    fromAgent(e) && typeof catalog === 'string' && catalog.length <= 1000 && validExtensionId(id)
      ? agentRelay.extCatalogInspect(catalog, id)
      : notAllowed
  )
  ipcMain.handle('agent:rules:forget', (e, project: unknown, command: unknown) => {
    if (!fromAgent(e)) return notAllowed
    if (typeof project !== 'string' || !project.trim() || project.length > 1000 ||
        typeof command !== 'string' || !command.trim() || command.length > 20000) {
      return { ok: false, error: tm('项目路径和命令要是非空文字，路径最多 1000 字，命令最多 20000 字。') }
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
    const res = await dialog.showOpenDialog(mainWin, { title: tm('选一个项目文件夹'), properties: ['openDirectory'] })
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
  // 界面语言：谁都能读；只有主窗口能改，改了通知所有窗口、重画托盘菜单、告诉 AI 服务
  ipcMain.handle('lang:get', () => ({ pref: loadSettings().language ?? 'auto', lang: mainLang() }))
  ipcMain.handle('lang:set', (e, pref: unknown) => {
    if (!fromMainWindow(e) || !isLangPref(pref)) return null
    saveSettings({ ...loadSettings(), language: pref })
    applyLanguage()
    broadcast('lang:changed', mainLang())
    updateTray()
    pushLanguage()
    return { pref, lang: mainLang() }
  })
  ipcMain.handle('ai:settings:put', async (e, update: AiSettingsUpdate) => {
    if (!fromMainWindow(e)) return MAIN_ONLY // 能改大模型地址和密钥：只认主窗口
    const res = await putAiSettings(AI_URL, update)
    if (res.ok) characterEvent({ kind: 'settings-saved' })
    return res
  })
  ipcMain.handle('ai:settings:test', (e) => (fromMainWindow(e) ? testLlm(AI_URL) : MAIN_ONLY))
  ipcMain.handle('ai:brain-models', (e) => (fromMainWindow(e) ? brainModels(AI_URL) : MAIN_ONLY))
  ipcMain.handle('skills:list', () => listSkills(AI_URL, account?.skillOverrides() ?? {}))
  ipcMain.on('skills:set', (e, id: string, on: boolean) => {
    if (!fromMainWindow(e)) return
    account?.setSkill(String(id), Boolean(on))
    void setPhone(AI_URL, { skills: account?.skillOverrides() ?? {} }) // 手机上聊天也按新的开关来
  })
  // 设置 → 手机：局域网的门开关、配对码、配过的手机。只认主窗口
  ipcMain.handle('phone:status', (e) => (fromMainWindow(e) ? getPhone(AI_URL) : MAIN_ONLY))
  ipcMain.handle('phone:enable', (e, on: unknown) =>
    fromMainWindow(e) && typeof on === 'boolean'
      ? setPhone(AI_URL, { enabled: on, skills: account?.skillOverrides() ?? {} })
      : MAIN_ONLY
  )
  ipcMain.handle('phone:pair', (e) => (fromMainWindow(e) ? newPhonePairing(AI_URL) : MAIN_ONLY))
  // 推到手机通知栏：哪家、地址（地址只往 AI 服务那边存，不回显）
  ipcMain.handle('phone:push', (e, kind: unknown, url: unknown) =>
    fromMainWindow(e) && typeof kind === 'string' && /^(none|ntfy|bark|wecom|feishu)$/.test(kind) && typeof url === 'string' && url.length <= 2000
      ? setPhone(AI_URL, { push: { kind, url } })
      : MAIN_ONLY
  )
  ipcMain.handle('phone:push-test', (e) => (fromMainWindow(e) ? testPhonePush(AI_URL) : MAIN_ONLY))
  // 日历和邮件：设置页的「试试」（只认主窗口）
  ipcMain.handle('agenda:test', (e, what: unknown) =>
    fromMainWindow(e) && (what === 'calendar' || what === 'mail') ? testAgenda(AI_URL, what) : MAIN_ONLY
  )
  // 定时任务（设置 → 编程）：只认主窗口；内容 AI 服务那边再检查一遍（项目只认登记过的、时间格式、权限）
  const scheduleId = (v: unknown): v is string => typeof v === 'string' && /^[0-9a-f]{1,16}$/.test(v)
  const scheduleBody = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && JSON.stringify(v).length < 10000
  ipcMain.handle('schedules:list', (e) => (fromMainWindow(e) ? listSchedules(AI_URL) : MAIN_ONLY))
  ipcMain.handle('schedules:add', (e, body: unknown) =>
    fromMainWindow(e) && scheduleBody(body) ? addSchedule(AI_URL, body as never) : MAIN_ONLY
  )
  ipcMain.handle('schedules:change', (e, id: unknown, body: unknown) =>
    fromMainWindow(e) && scheduleId(id) && scheduleBody(body) ? changeSchedule(AI_URL, id, body as never) : MAIN_ONLY
  )
  ipcMain.handle('schedules:delete', (e, id: unknown) => (fromMainWindow(e) && scheduleId(id) ? deleteSchedule(AI_URL, id) : MAIN_ONLY))
  ipcMain.handle('schedules:run', (e, id: unknown) => (fromMainWindow(e) && scheduleId(id) ? runSchedule(AI_URL, id) : MAIN_ONLY))
  // 「让她操作安卓手机」：开关、选手机、无线调试配对和连接。只认主窗口，参数在这里先检查一遍
  const adbAddress = (v: unknown): v is string => typeof v === 'string' && /^[\w.-]{1,253}:\d{1,5}$/.test(v)
  ipcMain.handle('android:status', (e) => (fromMainWindow(e) ? getAndroid(AI_URL) : MAIN_ONLY))
  ipcMain.handle('android:set', (e, body: unknown) => {
    if (!fromMainWindow(e) || !body || typeof body !== 'object') return MAIN_ONLY
    const b = body as { enabled?: unknown; serial?: unknown }
    const clean: { enabled?: boolean; serial?: string } = {}
    if (typeof b.enabled === 'boolean') clean.enabled = b.enabled
    if (typeof b.serial === 'string' && /^[\w.:-]{0,64}$/.test(b.serial)) clean.serial = b.serial
    return setAndroid(AI_URL, clean)
  })
  ipcMain.handle('android:pair', (e, address: unknown, code: unknown) =>
    fromMainWindow(e) && adbAddress(address) && typeof code === 'string' && /^\d{6}$/.test(code)
      ? pairAndroid(AI_URL, address, code)
      : MAIN_ONLY
  )
  ipcMain.handle('android:connect', (e, address: unknown) =>
    fromMainWindow(e) && adbAddress(address) ? connectAndroid(AI_URL, address) : MAIN_ONLY
  )
  ipcMain.handle('phone:forget', (e, id: unknown) =>
    fromMainWindow(e) && typeof id === 'string' && /^[0-9a-f]{1,32}$/.test(id) ? forgetPhone(AI_URL, id) : MAIN_ONLY
  )
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
  // 让她走到某处：只认角色窗口（对话就在她那里），目标只认名单里的
  ipcMain.on('character:move', (e, target: unknown) => {
    if (!win || e.sender !== win.webContents || !isMoveTarget(target)) return
    walkTo(target)
  })
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
  ipcMain.handle('discoveries:state', (e) => (fromMain(e) ? discoveries.state(AI_URL) : MAIN_ONLY))
  ipcMain.handle('discoveries:change', (e, source: unknown, action: unknown) => {
    if (!isDiscoverySource(source) || !(fromMain(e) || (fromCharacter(e) && action === 'less'))) return MAIN_ONLY
    if (action !== 'less' && action !== 'pause') return MAIN_ONLY
    return discoveries.change(AI_URL, source, action)
  })
  ipcMain.handle('discoveries:shown', async (e, id: unknown) =>
    fromCharacter(e) && isDiscoveryId(id) && (await discoveries.shown(AI_URL, id)).ok
  )
  ipcMain.on('discoveries:source', (e, source: unknown) => {
    if ((fromMain(e) || fromCharacter(e)) && isDiscoverySource(source)) void shell.openExternal(DISCOVERY_SOURCES[source])
  })
  // 她的作品、日记：看、删、打开、现在就做一件只认主窗口；气泡里点「看看」从角色窗口来，只是打开作品页
  const workOk = (id: unknown): id is string => typeof id === 'string' && WORK_ID.test(id)
  const nope = { ok: false, error: tm('只有主窗口能用') }
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
  ipcMain.handle('studio:reviews', (e) => (fromMain(e) ? studio.reviews(AI_URL) : nope))
  // 作品页里「现在写这周的」「重写」
  ipcMain.handle('studio:review-now', (e, force: unknown) => (fromMain(e) && desktop ? desktop.reviewNow(force === true) : nope))
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
  // 角色页面加载完模型来报到：要出场的话返回 true，她等落地（character:entrance land）再打招呼
  ipcMain.handle('character:ready', (e) => (fromCharacter(e) ? entranceReady() : false))
  // 开场演出：跳过（主窗口的按钮、Esc，或者她那边）、借不借鼠标（她的气泡）、再看一遍（设置页）
  ipcMain.on('intro:skip', (e) => {
    if (fromMain(e) || fromCharacter(e)) intro?.skip()
  })
  ipcMain.on('intro:answer', (e, value: unknown) => {
    if (fromCharacter(e) && (value === 'lend' || value === 'keep')) intro?.answer(value)
  })
  ipcMain.on('intro:replay', (e) => {
    if (fromMain(e) && intro && !intro.isRunning && win) void intro.start(true)
  })
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
          label: s.available ? s.name : tm('{name}（{why}）', { name: s.name, why: s.id === 'knowledge' ? tm('还没有资料') : tm('暂不可用') }),
          type: 'checkbox',
          checked: s.enabled,
          toolTip: s.description,
          click: () => account?.setSkill(s.id, !s.enabled)
        }))
      : [{ label: tm('（AI 服务没开）'), enabled: false }]
    if (!win) return
    Menu.buildFromTemplate([
      { label: tm('和她说话'), accelerator: CHAT_SHORTCUT, click: openChat },
      { label: tm('听我说'), accelerator: LISTEN_SHORTCUT, click: () => voiceMenu('listen') },
      { type: 'separator' },
      { label: tm('语音唤醒「你好艾拉」'), type: 'checkbox', checked: voice.wake, click: () => voiceMenu('toggle-wake') },
      { label: tm('出声说话'), type: 'checkbox', checked: voice.speak, click: () => voiceMenu('toggle-speak') },
      {
        label: tm('留意屏幕上的报错'),
        type: 'checkbox',
        checked: voice.watch,
        toolTip: tm('只看写代码的窗口（编辑器、终端），看到报错先问你'),
        click: () => voiceMenu('toggle-watch')
      },
      { type: 'separator' },
      { label: tm('技能包'), submenu: skillItems },
      { label: tm('导入扩展…'), click: () => openSettings('extensions') },
      { label: tm('打开艾拉'), click: () => openMain({ page: 'tasks' }) },
      { label: tm('项目和地图…'), click: () => openMain({ page: 'projects', tab: 'map' }) },
      { label: tm('资料库…'), click: () => openMain({ page: 'knowledge' }) },
      { label: tm('她记得的事…'), click: () => openMain({ page: 'memory' }) },
      { type: 'separator' },
      { label: tm('今天做了什么'), click: () => void desktop?.reportNow() },
      { label: tm('看看剪贴板里的报错'), click: () => win?.webContents.send('clipboard:ask') },
      { label: tm('提醒…'), click: () => openSettings('desktop') },
      { label: tm('免打扰（先藏起来）'), accelerator: HIDE_SHORTCUT, click: () => toggleDnd(true) },
      { label: tm('设置…'), click: () => openSettings('model') },
      { label: tm('回到右下角'), click: resetPosition },
      // 也可以直接对她说「去右上角」「往左一点」
      { label: tm('去哪儿'), submenu: SPOTS.map((s) => ({ label: tm(SPOT_NAMES[s]), click: () => walkTo(s) })) },
      { type: 'separator' },
      { label: tm('开发者工具'), click: () => win?.webContents.openDevTools({ mode: 'detach' }) },
      { type: 'separator' },
      { label: tm('退出'), click: () => app.quit() }
    ]).popup({ window: win })
  })

  ipcMain.handle('model:get-last', () => readModel(loadSettings().modelPath))
}

/** 托盘：桌面上的她被挡住、或者拖到别的屏幕上时，从这里也能打开主窗口 */
function createTray(): void {
  // 「艾」字标（scripts/make-icon.mjs 生成）：普通屏幕用 16，高分屏用 32
  const icon = nativeImage.createFromBuffer(Buffer.from(TRAY_16, 'base64'), { scaleFactor: 1 })
  icon.addRepresentation({ scaleFactor: 2, buffer: Buffer.from(TRAY_32, 'base64') })
  tray = new Tray(icon)
  tray.on('click', () => openMain({ page: 'tasks' }))
  updateTray()
}

/** 托盘菜单跟着免打扰的状态变 */
function updateTray(): void {
  if (!tray) return
  const dnd = desktop?.dnd ?? false
  const shown = desktop?.prefs.showCharacter ?? true
  tray.setToolTip(!shown ? tm('艾拉（不在桌面上）') : dnd ? tm('艾拉（免打扰中）') : tm('艾拉'))
  tray.setContextMenu(
    Menu.buildFromTemplate([
      { label: tm('打开艾拉'), click: () => openMain({ page: 'tasks' }) },
      // 不显示她的时候也能说话：临时叫出来，对话框关了再藏回去
      { label: tm('和她说话'), accelerator: CHAT_SHORTCUT, click: openChat },
      { label: tm('桌面上显示她'), type: 'checkbox', checked: shown, click: () => setShowCharacter(!shown) },
      { label: tm('免打扰（藏起来）'), type: 'checkbox', checked: dnd, enabled: shown, accelerator: HIDE_SHORTCUT, click: () => toggleDnd() },
      { label: tm('让她回到右下角'), enabled: shown, click: resetPosition },
      { label: tm('导入扩展…'), click: () => openSettings('extensions') },
      { type: 'separator' },
      { label: tm('退出'), click: () => app.quit() }
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
  applyLanguage() // 界面语言：菜单、托盘要用，先定下来
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
      // 日历上今天的会（没填订阅链接、取不到就当没有）
      const agenda = await getAgendaToday(AI_URL)
      const meetings = agenda.ok && agenda.data ? agenda.data.events : []
      return { sessions: r.data, memories: account?.memories.list() ?? [], messages: account?.history.visible() ?? [], meetings }
    },
    upcomingMeetings: async () => {
      const r = await getAgendaUpcoming(AI_URL, 10)
      return r.ok && r.data ? r.data.events : []
    },
    pollDiscoveries: async () => {
      const result = await discoveries.poll(AI_URL)
      return result.ok ? result.data ?? null : null
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
      writeDiary: async (day, facts) => (await studio.writeDiary(AI_URL, day, facts)).ok,
      writeReview: (body) => studio.writeReview(AI_URL, body)
    }
  })
  void serviceKeeper.start()
  firstMeet = !loadSettings().life?.firstSeen // desktop.start() 会记下今天是第一次见面，先看一眼
  intro = createIntro()
  createWindow()
  createTray()
  desktop.start()
  // 自动检查更新：装好的版本才查，开着开关，一天最多一次；启动一分钟后再查，不和启动抢网络
  setTimeout(() => {
    if (app.isPackaged && desktop?.prefs.checkUpdates && dueForCheck(loadSettings().update?.lastCheck)) void checkForUpdate(false)
  }, 60_000)
  // 设置里关了「桌面上显示她」：打开艾拉就是打开主窗口（不然只剩托盘图标，像是没打开）
  if (!desktop.prefs.showCharacter) openMain({ page: 'tasks' })
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
