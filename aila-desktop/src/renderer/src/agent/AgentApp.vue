<script setup lang="ts">
/**
 * 工作窗口：在这里给她编程或电脑任务；电脑任务不挂项目，只提供只读和先问再改。
 * 左边是会话（终端里用 aila 开的也在这里；能搜、改名、删除），中间是她做事的过程，右边是这次任务的待办、改过的文件和用量。
 * 输入框下面的「审查改动」（或者输入 /review）让她只读地审一遍没提交的改动。
 * 右边的「项目记忆」是她在这个项目里记下的约定（你也能加、删），下次在这个项目开任务时放进开头。
 * 长任务：「出方案」（或者输入 /plan 目标）让她先只读地交一份分阶段的计划，你批准后一阶段一阶段做下去；
 * 计划和进度从日志推出来（plan.ts），右边有计划进度和「接着做」。
 */
import { t as tr, t as tr2, localized, joinList } from '../i18n'
import { computed, nextTick, onActivated, onBeforeUnmount, onDeactivated, onMounted, reactive, ref, shallowRef, watch } from 'vue'
import { agentModes, MODE_HINTS as MODE_HINTS_RAW, MODE_LABELS as MODE_LABELS_RAW, type AgentCommandList, type AgentKind, type AgentLedger, type AgentMemoryNote, type AgentMode, type AgentSessionSummary } from '../../../shared/agent'
const MODE_LABELS = localized(MODE_LABELS_RAW)
const MODE_HINTS = localized(MODE_HINTS_RAW)
import { commandMenu, pickOnEnter, SOURCE_LABEL, type MenuItem } from './commands'
import { Transcript, endStats, endText, type EndItem, type ToolItem, type UndoPreview } from './fold'
import { renderMarkdown } from './markdown'
import { newTaskRequest } from '../shell/intents'
import { createAgentSource, type ProjectChoice } from './source'
import { buildTrace } from './trace'
import LedgerPanel from './LedgerPanel.vue'
import ToolCard from './ToolCard.vue'
import TraceView from './TraceView.vue'
import UndoResult from './UndoResult.vue'
import BudgetControls from './BudgetControls.vue'
import { parseBudget, validBudget } from '../../../shared/budget'
import Icon from '../theme/Icon.vue'
import { deleteNotice, groupSessions, sessionLabel } from './sessions'
import { canContinue, planState } from './plan'
import PlanCard from './PlanCard.vue'
import TaskBoard from './TaskBoard.vue'

const source = createAgentSource()
/** 告诉主窗口外壳有几个会话在等你（图标栏的角标） */
const emit = defineEmits<{ waiting: [count: number] }>()
const sessions = ref<AgentSessionSummary[]>([])
const current = ref<AgentSessionSummary | null>(null)
const t = shallowRef(reactive(new Transcript()))
const text = ref('')
const error = ref('')
const picking = ref(false)
// 主窗口 Ctrl+K 里点了「开新任务」
watch(newTaskRequest, () => (picking.value = true))
const projects = ref<ProjectChoice[]>([])
const newMode = ref<AgentMode>('ask')
const newKind = ref<AgentKind>('project')
const creating = ref(false)
const newModes = computed(() => agentModes(newKind.value))
const currentModes = computed(() => agentModes(current.value?.kind))
watch(newKind, (kind) => {
  if (kind === 'computer') {
    if (newMode.value === 'auto') newMode.value = 'ask'
    newIsolate.value = false
  }
})
/** 新任务在新分支上做（git worktree，测试通过才合并回来） */
const newIsolate = ref(false)
const newBudget = ref('')
/** 新任务：先出方案再动手 */
const newPlan = ref(false)
const newBudgetError = ref('')
const merging = ref('')
const models = ref<{ coder: string; chat: string; keySet: boolean } | null>(null)
const scroller = ref<HTMLElement>()
const input = ref<HTMLTextAreaElement>()
const streaming = reactive(new Set<string>())
const opening = ref(false)
let openRequest = 0
let openingSid = ''
const undoing = reactive(new Set<string>())
const undoDialog = ref<HTMLDialogElement>()
const undoPreview = ref<{ sid: string; plan: UndoPreview; branch: string | null } | null>(null)

// 左边列表：搜索、改名、删除
const query = ref('')
const groups = computed(() => groupSessions(sessions.value))
const WAITING: Record<string, string> = localized({ confirm: '等你确认', plan: '等你看计划', paused: '计划停了' })
const waitingCount = computed(() => sessions.value.filter((s) => s.waiting).length)
watch(waitingCount, (n) => emit('waiting', n), { immediate: true })
/** 终端里开的、别的窗口开的会话也可能在等你：每隔一会儿刷新一次列表 */
const LIST_REFRESH_MS = 15000
const BOARD_REFRESH_MS = 2500
let listTimer = 0
const menuFor = ref('')
const renaming = ref('')
const renameText = ref('')
const renameInput = ref<HTMLInputElement[]>()
const deleteDialog = ref<HTMLDialogElement>()
const deleteTarget = ref<AgentSessionSummary | null>(null)
const deleting = ref(false)
let searchTimer = 0

// 右边：项目记忆（按项目，不按会话）
const notes = ref<AgentMemoryNote[]>([])
const noteText = ref('')
const noteBusy = ref(false)
let notesFor = ''

// 长任务的计划
const plan = computed(() => planState(t.value.events))
/** 最新一张「交计划」卡片：只有它显示成计划卡片，之前交的照普通工具卡片显示 */
const latestPlanKey = computed(() => {
  for (let i = t.value.items.length - 1; i >= 0; i--) {
    const item = t.value.items[i]
    if (item.kind === 'tool' && item.name === 'propose_plan' && item.status === 'ok') return item.key
  }
  return ''
})
const planBusy = ref(false)
/** 新任务里勾了「先出方案」：下一句话当作目标，让她先出计划 */
const planNext = ref(false)

const running = computed(() => t.value.running)
const pending = computed(() => t.value.pending)
const files = computed(() => [...t.value.files.entries()])
const usage = computed(() => t.value.usage)
const undoBusy = computed(() => !!current.value && undoing.has(current.value.id))
const undoTarget = computed(() => {
  const sid = current.value?.id
  if (!sid || opening.value || merging.value || streaming.has(sid)) return undefined
  return t.value.undoTarget
})

// ---------- 执行轨迹、上下文账本 ----------

/** 中间看什么：过程（对话和卡片）还是轨迹（时间线） */
const view = ref<'chat' | 'trace'>('chat')
/** 正在做的一轮，时间线要一直往右长：每秒走一下 */
const now = ref(Date.now() / 1000)
let clock = 0
const trace = computed(() => buildTrace(t.value.events, running.value ? now.value : undefined))
const ledger = ref<AgentLedger | null>(null)
let ledgerTimer = 0

/** 上下文账本：每一步结束、换会话、压缩以后重新取（连着来的合成一次） */
function loadLedger(): void {
  clearTimeout(ledgerTimer)
  ledgerTimer = window.setTimeout(async () => {
    const sid = current.value?.id
    if (!sid) return
    try {
      const l = await source.context(sid)
      if (current.value?.id === sid) ledger.value = l
    } catch {
      // 取不到就先不显示，下一步再取
    }
  }, 300)
}
const modelLabel = computed(() => {
  const m = models.value
  if (!m) return ''
  if (!m.keySet) return tr('假模型（还没填密钥）')
  return m.coder ? m.coder : tr('{model}（和对话一样）', { model: m.chat })
})
const choices = computed<ProjectChoice[]>(() => {
  const seen = new Set<string>()
  const out: ProjectChoice[] = []
  for (const p of [...sessions.value.filter((s) => s.kind !== 'computer').map((s) => ({ name: basename(s.cwd), path: s.cwd })), ...projects.value]) {
    const key = p.path.toLowerCase().replace(/[\\/]+$/, '')
    if (!seen.has(key)) {
      seen.add(key)
      out.push(p)
    }
  }
  return out.slice(0, 12)
})

// ---------- 会话 ----------

let listRequest = 0
/**
 * 重新取会话列表。loud：你自己触发的（打开页面、搜索）出错要说；后台刷新（定时、事件触发、看板轮询）
 * 偶尔一次失败不打扰你，下一次刷新就补上了
 */
async function refreshList(loud = false): Promise<void> {
  const request = ++listRequest
  try {
    const result = await source.list(query.value)
    if (request !== listRequest) return
    sessions.value = result
    const active = current.value
    const latest = active && result.find((session) => session.id === active.id)
    if (active && latest && latest.events > t.value.lastSeq) {
      active.cost = latest.cost ?? null
      t.value.budget = validBudget(latest.budget) ? latest.budget : null
    }
  } catch (err) {
    if (request === listRequest && loud) error.value = (err as Error).message
  }
}

async function open(sid: string): Promise<void> {
  const request = ++openRequest
  openingSid = sid
  opening.value = true
  closeUndoPreview()
  error.value = ''
  if (!sessions.value.some((s) => s.id === sid)) void refreshList() // 刚从别处交过来的任务
  try {
    const s = await source.get(sid)
    if (request !== openRequest) return
    const tr = reactive(new Transcript())
    for (const e of s.events) tr.apply(e)
    // 刷新同一个会话时，保留请求期间已经收到的事件（包括撤销结果）。
    if (current.value?.id === sid) for (const e of t.value.events) tr.apply(e)
    t.value = tr
    current.value = { ...s, events: s.events.length }
    void refreshList() // 详情请求期间可能又记了用量，重新取服务端累计费用
    picking.value = false
    ledger.value = null
    loadLedger()
    void loadNotes()
    void loadCommands()
    if (s.running && !streaming.has(sid)) {
      streaming.add(sid)
      source.watch(sid, tr.lastSeq) // 正在做（可能是终端里开的）：接上实时事件
    }
    await nextTick()
    scrollToBottom()
    input.value?.focus()
  } catch (err) {
    if (request === openRequest) error.value = (err as Error).message
  } finally {
    if (request === openRequest) opening.value = false
  }
}

async function startNew(path?: string): Promise<void> {
  if (creating.value) return
  error.value = ''
  newBudgetError.value = ''
  let budget: number | null
  try { budget = parseBudget(newBudget.value) }
  catch (err) { newBudgetError.value = (err as Error).message; return }
  creating.value = true
  try {
    const computer = newKind.value === 'computer'
    const mode = computer && newMode.value === 'auto' ? 'ask' : newMode.value
    const s = await source.create(computer ? undefined : path, mode, computer ? false : newIsolate.value, budget, newKind.value)
    await refreshList()
    await open(s.id)
    planNext.value = newPlan.value
  } catch (err) {
    error.value = (err as Error).message
  } finally {
    creating.value = false
  }
}

function changedBudget(sid: string): void {
  // HTTP 保存结果可能先于实时流到达，重读完整日志，不能用单条事件跳过中间记录。
  if (current.value?.id === sid && (!opening.value || openingSid === sid)) void open(sid)
  else void refreshList()
}

async function pickOther(): Promise<void> {
  const path = await source.pickFolder()
  if (path) await startNew(path)
}

// ---------- 搜索、改名、删除 ----------

// 打字停一下再搜（服务端按标题和项目路径找，能找到很早的会话）
watch(query, () => {
  clearTimeout(searchTimer)
  searchTimer = window.setTimeout(() => void refreshList(true), 250)
})

function toggleMenu(sid: string): void {
  menuFor.value = menuFor.value === sid ? '' : sid
}

async function startRename(s: AgentSessionSummary): Promise<void> {
  menuFor.value = ''
  renaming.value = s.id
  renameText.value = s.title
  await nextTick()
  renameInput.value?.[0]?.focus()
  renameInput.value?.[0]?.select()
}

async function saveRename(s: AgentSessionSummary): Promise<void> {
  if (renaming.value !== s.id) return
  const title = renameText.value.trim()
  renaming.value = ''
  if (!title || title === s.title) return
  try {
    const saved = await source.rename(s.id, title)
    s.title = saved.title
    if (current.value?.id === s.id) current.value.title = saved.title
  } catch (err) {
    error.value = (err as Error).message
  }
}

async function askDelete(s: AgentSessionSummary): Promise<void> {
  menuFor.value = ''
  if (s.running) {
    error.value = tr('她还在做这个任务：先打断，再删除。')
    return
  }
  deleteTarget.value = s
  await nextTick()
  if (!deleteDialog.value?.open) deleteDialog.value?.showModal()
}

function closeDelete(): void {
  deleteTarget.value = null
  deleteDialog.value?.close()
}

async function deleteNow(): Promise<void> {
  const target = deleteTarget.value
  if (!target || deleting.value) return
  deleting.value = true
  try {
    await source.remove(target.id)
    closeDelete()
    if (current.value?.id === target.id) {
      current.value = null
      t.value = reactive(new Transcript())
      ledger.value = null
      notes.value = []
    }
    await refreshList()
    // 删完了就停在「还没有任务」那一页（上面有新任务按钮），不硬弹新任务的框
    if (!current.value && sessions.value.length) await open(sessions.value[0].id)
  } catch (err) {
    closeDelete()
    error.value = (err as Error).message
  } finally {
    deleting.value = false
  }
}

function closeMenus(): void {
  menuFor.value = ''
}

// ---------- 项目记忆 ----------

async function loadNotes(): Promise<void> {
  const cwd = current.value?.kind === 'computer' ? undefined : current.value?.cwd
  notesFor = cwd ?? ''
  if (!cwd) {
    notes.value = []
    return
  }
  try {
    const list = await source.memory(cwd)
    if (notesFor === cwd) notes.value = list
  } catch {
    // 取不到就先不显示，下次打开会话再取
  }
}

const replacingNote = ref<AgentMemoryNote | null>(null)
watch(() => current.value?.cwd, () => {
  replacingNote.value = null
  noteText.value = ''
})

function editNote(note: AgentMemoryNote): void {
  replacingNote.value = note
  noteText.value = note.text
}

function cancelEditNote(): void {
  replacingNote.value = null
  noteText.value = ''
}

async function addNote(): Promise<void> {
  if (current.value?.kind === 'computer') return
  const cwd = current.value?.cwd
  const text = noteText.value.trim()
  if (!cwd || !text || noteBusy.value) return
  noteBusy.value = true
  try {
    await source.remember(cwd, text, replacingNote.value?.id)
    cancelEditNote()
    await loadNotes()
  } catch (err) {
    error.value = (err as Error).message
  } finally {
    noteBusy.value = false
  }
}

async function forgetNote(note: AgentMemoryNote): Promise<void> {
  if (current.value?.kind === 'computer') return
  const cwd = current.value?.cwd
  if (!cwd) return
  try {
    await source.forgetMemory(cwd, note.id)
  } catch (err) {
    error.value = (err as Error).message
  }
  await loadNotes()
}

// ---------- 说话、确认、打断 ----------

/** 现在能不能开始新的一轮（说话或者审查） */
function canStart(): boolean {
  if (!current.value || undoBusy.value || opening.value || undoPreview.value || t.value.closed) return false
  if (running.value) {
    error.value = tr('她还在做事：等她做完，或者先按 Esc 打断。')
    return false
  }
  return true
}

function send(): void {
  const msg = text.value.trim()
  if (!msg) return
  // 「/review 重点」= 点「审查改动」
  const command = /^\/review(?:\s+([\s\S]*))?$/.exec(msg)
  if (command) return review(command[1] ?? '')
  const planCommand = /^\/plan(?:\s+([\s\S]*))?$/.exec(msg)
  if (planCommand) return proposePlan(planCommand[1] ?? '')
  if (planNext.value) return proposePlan(msg)
  if (!canStart() || !current.value) return
  error.value = ''
  text.value = ''
  streaming.add(current.value.id)
  source.send(current.value.id, msg)
  void nextTick(scrollToBottom)
}

/** 出方案：这一轮只读，goal 是目标；她交计划以后等你批准 */
function proposePlan(goal: string): void {
  if (!goal.trim()) {
    error.value = tr('先在输入框里写下要做的大目标，再点「出方案」。')
    return
  }
  if (!canStart() || !current.value) return
  error.value = ''
  text.value = ''
  planNext.value = false
  streaming.add(current.value.id)
  source.send(current.value.id, goal.trim(), false, true)
  void nextTick(scrollToBottom)
}

/** 批准计划并开始做 / 停下以后接着做 / 不要这份计划 */
async function planAction(action: 'approve' | 'continue' | 'reject', pauseEach = false): Promise<void> {
  const sid = current.value?.id
  if (!sid || planBusy.value) return
  if (action !== 'reject' && !canStart()) return
  planBusy.value = true
  error.value = ''
  try {
    // 只读模式下按计划做什么都改不了：批准时先换成「先问再改」（按钮上写明了）
    if (action === 'approve' && t.value.mode === 'read_only') await setMode('ask')
    if (action !== 'reject') streaming.add(sid)
    await source.plan(sid, action, pauseEach)
    void refreshList()
  } catch (err) {
    streaming.delete(sid)
    error.value = (err as Error).message
  } finally {
    planBusy.value = false
  }
  void nextTick(scrollToBottom)
}

/** /review：她只读地审一遍没提交的改动，按严重程度列出问题；focus 是可选的「特别注意什么」 */
function review(focus = ''): void {
  if (current.value?.kind === 'computer') {
    error.value = tr('电脑任务没有项目代码可审。要审改动，在项目里开一个任务。')
    return
  }
  if (!canStart() || !current.value) return
  error.value = ''
  text.value = ''
  streaming.add(current.value.id)
  source.send(current.value.id, focus.trim(), true)
  void nextTick(scrollToBottom)
}

async function answer(item: ToolItem, approved: boolean, remember: boolean): Promise<void> {
  if (!item.confirm || item.confirm.result) return
  if (!(await source.confirm(item.confirm.id, approved, remember))) error.value = tr('这个确认已经结束了（超时或者被打断）。')
}

async function interrupt(): Promise<void> {
  if (!current.value || !running.value) return
  try {
    await source.interrupt(current.value.id)
  } catch (err) {
    error.value = (err as Error).message
  }
}

async function setMode(mode: AgentMode): Promise<void> {
  if (!current.value) return
  if (!currentModes.value.includes(mode)) return
  try {
    await source.setMode(current.value.id, mode)
    if (!streaming.has(current.value.id)) t.value.apply({ type: 'mode', mode }) // 没有事件流时自己记一笔
  } catch (err) {
    error.value = (err as Error).message
  }
}

// ---------- 在新分支上做 ----------

async function mergeNow(runTests: boolean): Promise<void> {
  if (!current.value || running.value || merging.value || undoBusy.value) return
  merging.value = runTests ? tr('正在跑测试……') : tr('正在合并……')
  error.value = ''
  try {
    t.value.apply(await source.merge(current.value.id, runTests))
    void nextTick(scrollToBottom)
  } catch (err) {
    error.value = (err as Error).message
  } finally {
    merging.value = ''
  }
}

async function discardNow(): Promise<void> {
  if (!current.value || running.value || undoBusy.value) return
  if (!window.confirm(tr('放弃分支 {branch}？她在分支上还没合并的改动会删掉，你的代码保持原样。', { branch: t.value.branch ?? '' }))) return
  try {
    t.value.apply(await source.discard(current.value.id))
    void refreshList()
  } catch (err) {
    error.value = (err as Error).message
  }
}

async function compactNow(): Promise<void> {
  if (!current.value || running.value || undoBusy.value) return
  try {
    await source.compact(current.value.id)
    await open(current.value.id)
  } catch (err) {
    error.value = (err as Error).message
  }
}

// ---------- 撤销最近一轮 ----------

function closeUndoPreview(): void {
  undoPreview.value = null
  undoDialog.value?.close()
}

async function previewUndo(item: EndItem): Promise<void> {
  const sid = current.value?.id
  if (!sid || undoBusy.value || !item.undo || undoTarget.value?.key !== item.key || undoPreview.value) return
  undoPreview.value = { sid, plan: item.undo, branch: t.value.branch }
  await nextTick()
  if (undoPreview.value?.sid === sid && !undoDialog.value?.open) undoDialog.value?.showModal()
}

async function undoNow(): Promise<void> {
  const preview = undoPreview.value
  if (!preview) return
  const { sid, plan } = preview
  closeUndoPreview()
  if (current.value?.id !== sid || undoing.has(sid)) return
  if (undoTarget.value?.undo?.turnSeq !== plan.turnSeq) {
    error.value = tr('最近一轮已经变了，重新查看再撤销。')
    return
  }
  undoing.add(sid)
  error.value = ''
  try {
    const result = await source.undo(sid, plan.turnSeq)
    if (current.value?.id === sid) {
      t.value.apply(result)
      loadLedger()
      void nextTick(scrollToBottom)
    }
    void refreshList()
  } catch (err) {
    if (current.value?.id === sid && !opening.value) {
      // 服务拒绝时重读会话，避免继续显示已经过时的轮次。
      await open(sid)
      if (current.value?.id === sid && !opening.value) error.value = (err as Error).message
    }
  } finally {
    undoing.delete(sid)
  }
}

source.onEvent((sid, e) => {
  if (sid !== current.value?.id) {
    // 别的会话：结束、要你确认、计划停了，都会改变列表上的「等你」
    if (['turn_end', 'confirm', 'confirm_end', 'plan_pause', 'plan_end'].includes(e.type)) void refreshList()
    return
  }
  if (['confirm', 'confirm_end', 'plan_pause', 'plan_end', 'plan_decision'].includes(e.type)) void refreshList()
  const near = nearBottom()
  t.value.apply(e)
  if (e.type === 'user' || e.type === 'undo' || e.type === 'branch_closed') closeUndoPreview()
  if (e.type === 'user' || e.type === 'usage' || e.type === 'turn_end' || e.type === 'undo' || e.type === 'budget') void refreshList()
  if (['usage', 'turn_end', 'compaction', 'tool', 'undo'].includes(e.type)) loadLedger()
  if (e.type === 'tool' && e.memory) void loadNotes() // 她刚记了一条
  if (near) void nextTick(scrollToBottom)
})
source.onStreamEnd((sid, err) => {
  streaming.delete(sid)
  if (err && sid === current.value?.id) error.value = err
})
source.onFocus((sid) => {
  void source.takeFocus() // 已经开着：直接切过去，主进程那边记着的这个也清掉，免得下次打开又跳回来
  void open(sid)
})

// ---------- 看板：几个任务同时做 ----------

const board = ref(false)
const boardNow = ref(Date.now() / 1000)
/** 侧栏「看板」按钮上的数字：在做和等你的任务一共几个 */
const boardBusy = computed(() => sessions.value.filter((s) => s.running || s.waiting).length)
let boardTimer = 0

watch(board, (on) => {
  window.clearInterval(boardTimer)
  if (!on) return
  void refreshList()
  // 看板开着时勤快一点刷：别的任务在做什么只能靠列表摘要
  boardTimer = window.setInterval(() => {
    boardNow.value = Date.now() / 1000
    void refreshList()
  }, BOARD_REFRESH_MS)
})

function openFromBoard(sid: string): void {
  board.value = false
  void open(sid)
}

async function answerFromBoard(_sid: string, id: string, approved: boolean): Promise<void> {
  if (!(await source.confirm(id, approved, false))) error.value = tr('这个确认已经结束了（超时或者被打断）。')
  void refreshList()
}

/** 一次开几个：每个都在新分支上做（互不干扰），开好就开始 */
async function batchFromBoard(cwd: string, mode: AgentMode, tasks: string[]): Promise<void> {
  error.value = ''
  for (const task of tasks) {
    try {
      const s = await source.create(cwd, mode, true, null, 'project')
      streaming.add(s.id)
      source.send(s.id, task)
    } catch (err) {
      error.value = tr('「{task}」没开成：{why}', { task, why: (err as Error).message })
      break
    }
  }
  await refreshList()
}

// ---------- 命令菜单：输入「/」时列出内置命令和自定义命令 ----------

const commandList = ref<AgentCommandList | null>(null)
const menuIndex = ref(0)
/** 按了 Esc：在输入变化前不再弹 */
const menuClosed = ref(false)
const menu = computed<MenuItem[]>(() =>
  menuClosed.value || !current.value || running.value
    ? []
    : commandMenu(text.value, commandList.value?.commands ?? [], current.value.kind === 'computer')
)
/** 展开看过「发了什么」的自定义命令 */
const openCommands = reactive(new Set<string>())

async function loadCommands(): Promise<void> {
  const s = current.value
  if (!s || !source.commands) return
  const kind = s.kind ?? 'project'
  try {
    const list = await source.commands(kind === 'computer' ? null : s.cwd, kind)
    if (current.value?.id === s.id) commandList.value = list
  } catch {
    commandList.value = null // 取不到（旧服务）：只列内置的
  }
}

watch(text, (now, before) => {
  menuIndex.value = 0
  menuClosed.value = false
  if (now === '/' && before !== '/') void loadCommands() // 刚打「/」：重新读一遍，命令文件可能刚加
})

function pickCommand(item: MenuItem): void {
  text.value = `/${item.name} `
  void nextTick(() => input.value?.focus())
}

function toggleCommand(key: string): void {
  if (openCommands.has(key)) openCommands.delete(key)
  else openCommands.add(key)
}

/** 菜单开着时：↑↓ 选、Tab 补全、Enter 补全（已经是完整命令名就照常发送）、Esc 收起。处理了返回 true */
function menuKey(e: KeyboardEvent): boolean {
  const items = menu.value
  if (!items.length || e.isComposing) return false
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    menuIndex.value = (menuIndex.value + (e.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length
  } else if (e.key === 'Tab') {
    pickCommand(items[menuIndex.value])
  } else if (e.key === 'Escape') {
    menuClosed.value = true
    e.stopPropagation() // 不要变成「打断她」
  } else if (e.key === 'Enter' && !e.shiftKey) {
    const next = pickOnEnter(text.value, items[menuIndex.value])
    if (next === null) return false
    text.value = next
  } else {
    return false
  }
  e.preventDefault()
  return true
}

function onComposerKey(e: KeyboardEvent): void {
  if (menuKey(e)) return
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault()
    send()
  }
}

function onKey(e: KeyboardEvent): void {
  if (undoDialog.value?.open || deleteDialog.value?.open) return // 对话框里的 Esc 只关对话框
  const typing = e.target instanceof HTMLTextAreaElement && text.value.length > 0
  const p = pending.value
  if (p?.confirm && !typing) {
    if (e.key === '1') return void (e.preventDefault(), answer(p, true, false))
    if (e.key === '2' && p.confirm.canRemember) return void (e.preventDefault(), answer(p, true, true))
    if (e.key === 'Escape') return void answer(p, false, false)
  }
  if (e.key === 'Escape') {
    if (picking.value) {
      if (!creating.value) picking.value = false
    } else void interrupt()
  }
}

// ---------- 小工具 ----------

function basename(path: string): string {
  return path.replace(/[\\/]+$/, '').split(/[\\/]/).pop() || path
}

function when(ts?: number): string {
  if (!ts) return ''
  const d = new Date(ts * 1000)
  const mins = (Date.now() - d.getTime()) / 60000
  if (mins < 1) return tr('刚刚')
  if (mins < 60) return tr('{n} 分钟前', { n: Math.floor(mins) })
  const today = new Date().toDateString() === d.toDateString()
  const hm = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  return today ? hm : `${d.getMonth() + 1}-${d.getDate()} ${hm}`
}

function nearBottom(): boolean {
  const el = scroller.value
  return !el || el.scrollHeight - el.scrollTop - el.clientHeight < 120
}

function scrollToBottom(): void {
  const el = scroller.value
  if (el) el.scrollTop = el.scrollHeight
}

const endMark: Record<string, string> = { done: '✓', error: '✗' }

onMounted(async () => {
  window.addEventListener('keydown', onKey)
  window.addEventListener('click', closeMenus)
  listTimer = window.setInterval(() => void refreshList(), LIST_REFRESH_MS)
  // 轨迹里正在做的那一轮每秒往右长一点（只在看轨迹、而且她在做事的时候）
  clock = window.setInterval(() => {
    if (running.value && view.value === 'trace') now.value = Date.now() / 1000
  }, 1000)
  if (import.meta.env.DEV) (window as unknown as Record<string, unknown>).__agent = { t, open, source }
  await refreshList(true)
  void source.projects().then((p) => (projects.value = p))
  void source.models().then((m) => (models.value = m))
  const focus = await source.takeFocus()
  if (focus) await open(focus)
  else if (sessions.value.length) await open(sessions.value[0].id)
  // 一个任务都没有：先看到任务页本身（说明 + 新任务按钮），不一进来就弹框
})

// 在主窗口里切到别的页面时，任务页还留着（KeepAlive）：这时不能再听 1 / 2 / Esc，
// 不然在设置里打个「1」就替你同意了
onActivated(() => window.addEventListener('keydown', onKey))
onDeactivated(() => {
  window.removeEventListener('keydown', onKey)
  closeUndoPreview()
  closeDelete()
  closeMenus()
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKey)
  window.removeEventListener('click', closeMenus)
  clearInterval(clock)
  clearInterval(boardTimer)
  clearTimeout(ledgerTimer)
  clearTimeout(searchTimer)
  clearInterval(listTimer)
})
</script>

<template>
  <div class="app" :class="{ bare: !current }">
    <aside class="side">
      <div class="side-head">
        <h2>{{ tr('任务') }}</h2>
        <div class="head-actions">
          <button class="board-toggle" :class="{ on: board }" :title="board ? tr('回到当前任务') : tr('看板：几个任务同时做时一眼看全')" @click="board = !board">
            {{ tr('看板') }}<span v-if="boardBusy" class="dot">{{ boardBusy }}</span>
          </button>
          <button class="new" :title="tr('开一个新任务')" @click="picking = true"><Icon name="plus" :size="15" :stroke="2" />{{ tr('新任务') }}</button>
        </div>
      </div>
      <label class="search">
        <Icon name="search" :size="15" />
        <input v-model="query" type="search" :placeholder="tr('找任务：标题或项目')" :aria-label="tr('找任务')" @keydown.stop @keydown.esc="query = ''" />
      </label>
      <div class="list">
        <template v-for="g in groups" :key="g.label">
          <div class="group">{{ g.label }}</div>
          <div
            v-for="s in g.items"
            :key="s.id"
            class="sess"
            :class="{ on: s.id === current?.id, menu: menuFor === s.id }"
            :title="s.cwd"
            @click="renaming !== s.id && open(s.id)"
          >
            <input
              v-if="renaming === s.id"
              ref="renameInput"
              v-model="renameText"
              class="rename"
              maxlength="100"
              :aria-label="tr('新标题')"
              @click.stop
              @keydown.stop
              @keydown.enter.prevent="saveRename(s)"
              @keydown.esc.prevent="renaming = ''"
              @blur="saveRename(s)"
            />
            <template v-else>
              <div class="title"><i v-if="s.running" class="live-dot" :title="tr('她在做')"></i>{{ s.title }}</div>
              <div class="meta">
                <span>{{ sessionLabel(s) }}</span>
                <span v-if="s.branch" class="meta-branch" :title="tr('在分支 {branch} 上做', { branch: s.branch })"><Icon name="branch" :size="12" /></span>
                <span class="dot">·</span>
                <span>{{ when(s.updated) }}</span>
                <span v-if="s.plan && s.plan.decision === 'approved' && !s.plan.finished" class="meta-plan">
                  {{ tr('第 {n}/{total} 阶段', { n: Math.min(s.plan.done + 1, s.plan.stages), total: s.plan.stages }) }}
                </span>
                <span v-if="s.waiting" class="meta-wait">{{ WAITING[s.waiting] }}</span>
              </div>
              <button class="more" :title="tr('改名、删除')" :aria-label="tr('改名、删除')" @click.stop="toggleMenu(s.id)"><Icon name="more" :size="16" :stroke="2.4" /></button>
              <div v-if="menuFor === s.id" class="menu-pop" role="menu" @click.stop>
                <button role="menuitem" @click="startRename(s)"><Icon name="pencil" :size="15" />{{ tr('改名') }}</button>
                <button role="menuitem" class="danger" @click="askDelete(s)"><Icon name="trash" :size="15" />{{ tr('删除') }}</button>
              </div>
            </template>
          </div>
        </template>
        <div v-if="!sessions.length" class="none">{{ query.trim() ? tr('没找到对得上的任务') : tr('还没有任务') }}</div>
      </div>
      <div class="side-foot">{{ tr('终端里也能用：') }}<code>aila</code></div>
    </aside>

    <TaskBoard
      v-if="board"
      class="main"
      :sessions="sessions"
      :choices="choices"
      :now="boardNow"
      :error="error"
      @open="openFromBoard"
      @stop="(sid: string) => void source.interrupt(sid)"
      @answer="answerFromBoard"
      @batch="batchFromBoard"
    />
    <main v-else class="main">
      <header v-if="current" class="top">
        <div class="proj" :title="current.cwd">
          <b>{{ sessionLabel(current) }}</b>
          <span class="path">{{ current.cwd }}</span>
        </div>
        <span v-if="t.branch" class="branch-badge" :title="tr('她在这个分支的临时目录里改，你的代码一个字都没动')"><Icon name="branch" :size="13" />{{ t.branch }}</span>
        <BudgetControls class="task-budget" :sid="current.id" :cost="current.cost" :budget="t.budget" :change="source.setBudget" @changed="changedBudget" />
        <select class="mode" :aria-label="tr('当前任务的权限模式')" :value="t.mode" :title="MODE_HINTS[t.mode]" @change="setMode(($event.target as HTMLSelectElement).value as AgentMode)">
          <option v-for="m in currentModes" :key="m" :value="m">{{ MODE_LABELS[m] }}</option>
        </select>
        <span class="model" :title="tr('编程用的模型：在「设置 → 对话」里改')" @click="source.openSettings()">{{ modelLabel }}</span>
        <div class="tabs">
          <button :class="{ on: view === 'chat' }" @click="view = 'chat'">{{ tr('过程') }}</button>
          <button :class="{ on: view === 'trace' }" :title="tr('按时间看每一步花了多久')" @click="view = 'trace'">{{ tr('轨迹') }}</button>
        </div>
      </header>

      <div v-if="current && view === 'trace'" class="scroll">
        <TraceView :turns="trace" />
      </div>
      <div v-else ref="scroller" class="scroll">
        <div v-if="current" class="transcript">
          <div v-if="!t.items.length" class="hello">
            <template v-if="current.kind === 'computer'">
              <p>{{ tr('要她在电脑上做什么？') }}</p>
              <p class="dim">{{ tr('比如「先看看测试文件夹里有哪些文件，给我一份按类型整理的方案」「用 PowerShell 读一个 Excel」。她通过命令和脚本操作，改动前会问你。') }}</p>
            </template>
            <template v-else>
              <p>{{ tr('在') }} <b>{{ basename(current.cwd) }}</b> {{ tr('里，要她做什么？') }}</p>
              <p class="dim">{{ tr('比如「这个项目是做什么的」「给购物车加一个打折函数，写好测试并跑通」「看看为什么 npm test 失败」') }}</p>
            </template>
          </div>
          <template v-for="item in t.items" :key="item.key">
            <div v-if="item.kind === 'user' && item.review" class="user review"><Icon name="review" :size="16" />{{ item.text }}<span>{{ tr('只读') }}</span></div>
            <div v-else-if="item.kind === 'user' && item.tag === 'plan'" class="user review"><Icon name="plan" :size="16" />{{ item.text }}<span>{{ tr('只读') }}</span></div>
            <div v-else-if="item.kind === 'user' && item.tag === 'stage'" class="stage-line"><Icon name="play" :size="12" :stroke="2" />{{ item.text }}</div>
            <div v-else-if="item.kind === 'user' && item.tag === 'command'" class="user command">
              <div class="command-head">
                <Icon name="command" :size="16" />{{ item.text }}
                <button class="link" type="button" @click="toggleCommand(item.key)">{{ openCommands.has(item.key) ? tr('收起') : tr('看发了什么') }}</button>
              </div>
              <pre v-if="openCommands.has(item.key)" class="command-body">{{ item.detail }}</pre>
            </div>
            <div v-else-if="item.kind === 'user'" class="user">{{ item.text }}</div>
            <!-- eslint-disable-next-line vue/no-v-html：renderMarkdown 先整段转义，只插入固定的几种标签 -->
            <div v-else-if="item.kind === 'text'" class="md" :class="{ streaming: item.live }" v-html="renderMarkdown(item.text)"></div>
            <PlanCard
              v-else-if="item.kind === 'tool' && item.key === latestPlanKey && plan"
              :plan="plan"
              :busy="planBusy || running"
              :read-only="t.mode === 'read_only'"
              @approve="(pause: boolean) => planAction('approve', pause)"
              @reject="planAction('reject')"
            />
            <ToolCard v-else-if="item.kind === 'tool'" :item="item" :session-id="current.id" :computer="current?.kind === 'computer'"
              :load-screenshot="source.screenshot" @answer="(a: boolean, r: boolean) => answer(item, a, r)" />
            <div v-else-if="item.kind === 'merge'" class="merge" :class="{ ok: item.merged }">
              <div class="merge-head">{{ item.merged ? tr('✓ 合并了') : tr('■ 没有合并') }}</div>
              <div>{{ item.reason }}</div>
              <ul v-if="item.tests.length" class="merge-tests">
                <li v-for="(tr, i) in item.tests" :key="i" :class="{ bad: !tr.ok }">
                  {{ tr.ok ? '✓' : '✗' }} {{ tr.label }} · {{ tr2('{n} 秒', { n: tr.seconds }) }}
                  <pre v-if="!tr.ok && tr.tail">{{ tr.tail }}</pre>
                </li>
              </ul>
              <div v-if="item.files.length" class="dim">{{ tr('文件：') }}{{ joinList(item.files) }}</div>
              <div v-if="item.noTests && t.branch" class="merge-actions">
                <button class="ghost" :disabled="!!merging" @click="mergeNow(false)">{{ tr('不跑测试，直接合并') }}</button>
              </div>
            </div>
            <div v-else-if="item.kind === 'end'" class="end" :class="item.reason">
              <span>{{ endMark[item.reason] ?? '■' }} {{ endText(item) }}</span>
              <span class="stats">{{ endStats(item) }}</span>
              <button v-if="undoTarget?.key === item.key" class="ghost undo-button" :disabled="undoBusy" @click="previewUndo(item)">
                {{ undoBusy ? tr('正在撤销…') : tr('撤销这一轮') }}
              </button>
            </div>
            <UndoResult v-else-if="item.kind === 'undo'" :result="item.result" />
            <div v-else class="note">{{ item.text }}</div>
          </template>
          <div v-if="running && !pending" class="working"><i></i><i></i><i></i>{{ tr('她在做……') }}<span>{{ tr('Esc 打断') }}</span></div>
        </div>
        <div v-else class="hello big">
          <div class="hello-mark" aria-hidden="true">&gt;_</div>
          <p>{{ tr('给她一个编程或电脑任务：她会读文件、跑命令，改动前按你选的权限来。') }}</p>
          <button class="new" @click="picking = true"><Icon name="plus" :size="15" :stroke="2" />{{ tr('新任务') }}</button>
        </div>
      </div>

      <div v-if="error" class="error" @click="error = ''">{{ error }}</div>

      <footer v-if="current" class="composer">
        <div class="box">
          <div v-if="menu.length" class="cmd-menu" role="listbox" :aria-label="tr('命令')">
            <button
              v-for="(c, i) in menu"
              :key="c.source + c.name"
              type="button"
              role="option"
              :aria-selected="i === menuIndex"
              :class="{ on: i === menuIndex }"
              @mousedown.prevent="pickCommand(c)"
              @mouseenter="menuIndex = i"
            >
              <b>/{{ c.name }}</b><span class="desc">{{ c.description }}</span><span class="src">{{ SOURCE_LABEL[c.source] }}</span>
            </button>
            <div v-if="!commandList?.commands.length" class="cmd-hint">
              {{ tr('常做的流程可以写成自定义命令：把「名字.md」放进') }}
              {{ commandList?.project_dir ? tr('项目的 .aila/commands/') : '' }}{{ commandList?.project_dir ? tr('，或者 ') : '' }}{{ commandList?.user_dir ?? tr('数据目录的 commands/') }}{{ tr('，以后输入「/名字」她就照着做') }}
            </div>
            <div class="cmd-keys">{{ tr('↑↓ 选 · Tab 补全 · Esc 收起') }}</div>
          </div>
          <textarea
            ref="input"
            v-model="text"
            rows="3"
            :disabled="t.closed"
            :placeholder="
              t.closed
                ? tr('这个会话的分支已经放弃了，点左上角「新任务」开一个新的')
                : running
                  ? tr('她在做事……想好的下一句可以先写着')
                  : planNext
                    ? tr('说一下大目标：她先出一份分阶段的计划给你看')
                    : tr('要她做什么？')
            "
            @keydown="onComposerKey"
          ></textarea>
          <div class="bar">
            <button
              class="chip"
              :class="{ on: planNext }"
              :disabled="running || undoBusy || opening || t.closed"
              :title="tr('大活先出方案：她只读地了解情况，交一份分阶段的计划给你批准，再一阶段一阶段做。也可以输入「/plan 目标」')"
              @click="proposePlan(text)"
            >
              <Icon name="plan" :size="15" />{{ tr('出方案') }}
            </button>
            <button
              v-if="current?.kind !== 'computer'"
              class="chip"
              :disabled="running || undoBusy || opening || t.closed"
              :title="tr('她只读地审一遍还没提交的改动，按严重程度列出问题。也可以输入「/review 要特别注意的地方」')"
              @click="review()"
            >
              <Icon name="review" :size="15" />{{ tr('审查改动') }}
            </button>
            <span class="keys">{{ tr('Enter 发送 · Shift+Enter 换行') }}</span>
            <button v-if="running" class="stop" @click="interrupt"><Icon name="stop" :size="13" :stroke="2.2" />{{ tr('停下') }} <kbd>Esc</kbd></button>
            <button v-else class="send" :disabled="!text.trim() || undoBusy || opening" @click="send">{{ tr('发送') }}<Icon name="send" :size="15" :stroke="2" /></button>
          </div>
        </div>
      </footer>
    </main>

    <aside v-if="current && !board" class="info">
      <section v-if="plan && plan.decision !== 'rejected'" class="plan-side">
        <h3>{{ tr('计划') }}</h3>
        <p class="plan-side-goal">{{ plan.goal }}</p>
        <ol class="plan-steps">
          <li v-for="(s, i) in plan.stages" :key="i" :class="s.status" :title="s.summary || s.goal">
            <i aria-hidden="true"></i>{{ s.title }}
          </li>
        </ol>
        <p v-if="plan.decision === 'proposed'" class="dim">{{ tr('等你在左边的计划卡片上决定') }}</p>
        <p v-else-if="plan.finished" class="plan-ok">{{ tr('全部做完了') }}</p>
        <p v-else-if="plan.paused" class="plan-paused">{{ tr('停下了：{why}', { why: plan.paused.text }) }}</p>
        <button
          v-if="canContinue(plan, running)"
          class="ghost plan-continue"
          :disabled="planBusy || undoBusy || opening"
          @click="planAction('continue')"
        >
          <Icon name="play" :size="12" :stroke="2" />{{ tr('接着做第 {n} 阶段', { n: (plan.current ?? 0) + 1 }) }}
        </button>
      </section>
      <section v-if="t.branch">
        <h3>{{ tr('分支') }}</h3>
        <p class="dim">{{ tr('她在') }} <code>{{ t.branch }}</code> {{ tr('上改，你的代码一个字都没动。觉得可以了就合并：先跑项目的测试，全过、而且你这期间没改过同样的文件，才写回你的代码（不替你提交）。') }}</p>
        <div class="branch-actions">
          <button class="merge-btn" :disabled="running || !!merging || undoBusy" @click="mergeNow(true)">{{ merging || tr('跑测试并合并') }}</button>
          <button class="ghost" :disabled="running || !!merging || undoBusy" @click="discardNow">{{ tr('放弃分支') }}</button>
        </div>
      </section>
      <section>
        <h3>{{ tr('待办') }}</h3>
        <ul v-if="t.todos.length" class="todos">
          <li v-for="(item, i) in t.todos" :key="i" :class="item.status">
            {{ item.status === 'completed' ? '☑' : item.status === 'in_progress' ? '◐' : '☐' }} {{ item.content }}
          </li>
        </ul>
        <p v-else class="dim">{{ tr('步骤多的任务她会先列出来') }}</p>
      </section>
      <section>
        <h3>{{ tr('改过的文件') }}</h3>
        <ul v-if="files.length" class="files">
          <li v-for="[path, n] in files" :key="path" :title="path">{{ path }}<span v-if="n > 1" class="dim"> ×{{ n }}</span></li>
        </ul>
        <p v-else class="dim">{{ tr('还没有') }}</p>
      </section>
      <section v-if="current?.kind !== 'computer'" class="memory">
        <h3>{{ tr('项目记忆') }}</h3>
        <ul v-if="notes.length" class="notes">
          <li v-for="n in notes" :key="n.id" :class="n.source" :title="n.source === 'agent' ? tr('她记的') : tr('你加的')">
            <span>{{ n.text }}</span>
            <button class="x" type="button" :title="tr('更正这条约定')" :aria-label="tr('更正这条约定')" @click="editNote(n)">{{ tr('更正') }}</button>
            <button class="x" :title="tr('删掉这条')" :aria-label="tr('删掉这条')" @click="forgetNote(n)"><Icon name="close" :size="12" :stroke="2" /></button>
          </li>
        </ul>
        <p v-else class="dim">{{ tr('她弄清了测试命令、代码约定这类事会记在这里，下次在这个项目开任务时带上。') }}</p>
        <p v-if="replacingNote" class="dim">{{ tr('正在更正：{text}', { text: replacingNote.text }) }}</p>
        <form class="note-add" @submit.prevent="addNote">
          <input v-model="noteText" maxlength="300" :placeholder="tr('自己加一条……')" :aria-label="tr('加一条项目记忆')" @keydown.stop />
          <button type="submit" :disabled="!noteText.trim() || noteBusy">{{ replacingNote ? tr('保存更正') : tr('记下') }}</button>
          <button v-if="replacingNote" type="button" @click="cancelEditNote">{{ tr('取消') }}</button>
        </form>
      </section>
      <section>
        <h3>{{ tr('上下文账本') }}</h3>
        <LedgerPanel :ledger="ledger" :steps="t.steps" :totals="usage" />
        <button class="ghost" :disabled="running || undoBusy" :title="tr('用一份交接说明代替前面的对话（快满时会自动做）')" @click="compactNow">{{ tr('压缩上下文') }}</button>
      </section>
    </aside>

    <dialog
      ref="undoDialog"
      class="undo-dialog"
      aria-labelledby="undo-heading"
      aria-describedby="undo-summary"
      @close="!undoDialog?.open && (undoPreview = null)"
    >
      <template v-if="undoPreview">
        <h2 id="undo-heading">{{ tr('撤销最近一轮？') }}</h2>
        <p id="undo-summary">{{ tr('以下文件会退回到这一轮开始前。你之后改过的文件会跳过。') }}</p>
        <ul>
          <li v-for="file in undoPreview.plan.files" :key="file.path">
            <code>{{ file.path }}</code> · {{ file.created ? tr('删除这一轮新建的文件') : tr('恢复原内容') }}
          </li>
        </ul>
        <p v-if="undoPreview.plan.missing.length" class="dim">
          {{ tr('未留底的文件会跳过：') }}{{ joinList(undoPreview.plan.missing) }}
        </p>
        <p v-if="undoPreview.branch" class="dim">
          {{ tr('只处理分支') }} <code>{{ undoPreview.branch }}</code> {{ tr('的临时目录，已经合并到项目里的改动需要另外处理。') }}
        </p>
        <p v-if="undoPreview.plan.commands" class="dim">{{ tr('这一轮还跑过命令，命令做的事无法撤销。') }}</p>
        <div class="undo-actions">
          <button type="button" class="ghost" autofocus @click="closeUndoPreview">{{ tr('取消') }}</button>
          <button type="button" class="ghost undo-confirm" @click="undoNow">{{ tr('确认撤销') }}</button>
        </div>
      </template>
    </dialog>

    <dialog
      ref="deleteDialog"
      class="undo-dialog"
      aria-labelledby="delete-heading"
      @close="!deleteDialog?.open && (deleteTarget = null)"
    >
      <template v-if="deleteTarget">
        <h2 id="delete-heading">{{ tr('删除「{title}」？', { title: deleteTarget.title }) }}</h2>
        <p v-for="line in deleteNotice(deleteTarget)" :key="line">{{ line }}</p>
        <div class="undo-actions">
          <button type="button" class="ghost" autofocus @click="closeDelete">{{ tr('取消') }}</button>
          <button type="button" class="ghost danger-btn" :disabled="deleting" @click="deleteNow">{{ deleting ? tr('正在删除…') : tr('删除') }}</button>
        </div>
      </template>
    </dialog>

    <div v-if="picking" class="overlay" @click.self="!creating && (picking = false)">
      <div class="picker">
        <h2>{{ tr('开一个新任务') }}</h2>
        <fieldset class="task-kinds" :disabled="creating" @keydown.stop>
          <legend>{{ tr('在哪里做？') }}</legend>
          <label :class="{ on: newKind === 'project' }"><input v-model="newKind" type="radio" value="project" name="task-kind" />{{ tr('在项目里编程') }}</label>
          <label :class="{ on: newKind === 'computer' }"><input v-model="newKind" type="radio" value="computer" name="task-kind" />{{ tr('不选项目，操作电脑') }}</label>
        </fieldset>
        <label v-if="newKind === 'project'" class="isolate" :class="{ on: newIsolate }">
          <input id="new-task-isolate" v-model="newIsolate" type="checkbox" :disabled="creating" @keydown.stop />
          <span>
            <b>{{ tr('在新分支上做') }}</b>
            {{ tr('她在一个临时的 git 分支里改，你的代码一个字都不动；你说合并时先跑测试，全过才写回来（要是 git 仓库）') }}
          </span>
        </label>
        <div class="modes" :class="{ computer: newKind === 'computer' }" @keydown.stop>
          <label v-for="m in newModes" :key="m" :class="{ on: newMode === m }">
            <input v-model="newMode" type="radio" :value="m" name="new-task-mode" :disabled="creating" />
            <b>{{ MODE_LABELS[m] }}</b><span>{{ MODE_HINTS[m] }}</span>
          </label>
        </div>
        <label class="isolate" :class="{ on: newPlan }">
          <input id="new-task-plan" v-model="newPlan" type="checkbox" :disabled="creating" @keydown.stop />
          <span>
            <b>{{ tr('先出方案再动手') }}</b>
            {{ tr('大活用这个：她先只读地了解情况，交一份分阶段的计划给你批准，再一阶段一阶段做下去，不受一轮步数的限制') }}
          </span>
        </label>
        <div class="new-budget">
          <label for="new-task-budget">{{ tr('花费上限（元，可以不填）') }}</label>
          <input
            id="new-task-budget"
            v-model="newBudget"
            @keydown.stop
            inputmode="decimal"
            :placeholder="tr('不限')"
            :aria-invalid="Boolean(newBudgetError)"
            aria-describedby="new-task-budget-help new-task-budget-error"
          />
          <p id="new-task-budget-help" class="dim">{{ tr('未知价格的用量无法完整计价。达到上限后在下一步前停下，单步可能超出上限。') }}</p>
          <p v-if="newBudgetError" id="new-task-budget-error" class="budget-error" role="alert">{{ newBudgetError }}</p>
        </div>
        <p v-if="newKind === 'computer'" class="dim">{{ tr('从你的用户目录开始，通过命令和脚本操作。没有自动权限；危险命令和受保护位置每次都要问。批量操作先列方案，移动、改名等命令无法靠「撤销这一轮」恢复。') }}</p>
        <p v-else class="dim">{{ tr('她只在这个文件夹里读写；删除、推送这类危险命令不管哪种模式都会先问你。') }}</p>
        <!-- 上面的选项先选好；点项目就开始，所以项目放在最后 -->
        <div v-if="newKind === 'project'" class="choices">
          <p class="choices-title">{{ creating ? tr('正在开任务…') : tr('在哪个项目里做？点一下就开始') }}</p>
          <button v-for="p in choices" :key="p.path" class="choice" :title="p.path" :disabled="creating" @click="startNew(p.path)">
            <b>{{ p.name }}</b><span>{{ p.path }}</span>
          </button>
          <button class="choice other" :disabled="creating" @click="pickOther">{{ tr('选择其他文件夹…') }}</button>
        </div>
        <p v-if="error" class="budget-error" role="alert">{{ error }}</p>
        <button v-if="newKind === 'computer'" class="new computer-start" :disabled="creating" @click="startNew()">{{ creating ? tr('正在开任务…') : tr('开电脑任务') }}</button>
        <button class="ghost" :disabled="creating" @click="picking = false">{{ tr('取消') }}</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 颜色都来自 theme/tokens.css；这里的短名字给子组件（工具卡片、轨迹、账本）继续用 */
.app {
  --bg: var(--ui-bg);
  --side: var(--ui-panel);
  --card: var(--ui-surface);
  --raised: var(--ui-raised);
  --code: var(--ui-code);
  --line: var(--ui-line);
  --line-strong: var(--ui-line-strong);
  --text: var(--ui-text);
  --text-2: var(--ui-text-2);
  --dim: var(--ui-muted);
  --accent: var(--ui-accent);
  --accent-bg: var(--ui-accent-soft);
  --accent-line: var(--ui-accent-line);
  --ok: var(--ui-ok);
  --warn: var(--ui-warn);
  --bad: var(--ui-bad);
  display: grid;
  grid-template-columns: 252px minmax(0, 1fr) 272px;
  height: 100%;
  background: var(--bg);
  color: var(--text);
}

/* 还没打开会话：没有右边那一栏 */
.app.bare {
  grid-template-columns: 252px minmax(0, 1fr);
}

/* 窗口窄的时候先收起右边那一栏 */
@media (max-width: 1060px) {
  .app,
  .app.bare {
    grid-template-columns: 216px minmax(0, 1fr);
  }

  .info {
    display: none;
  }
}

button {
  font: inherit;
  color: inherit;
  cursor: pointer;
}

button:disabled {
  opacity: 0.45;
  cursor: default;
}

button:focus-visible,
input:focus-visible,
select:focus-visible,
textarea:focus-visible {
  outline: none;
  box-shadow: var(--ui-ring);
}

kbd {
  margin-left: 4px;
  padding: 0 5px;
  border: 1px solid var(--line-strong);
  border-bottom-width: 2px;
  border-radius: 4px;
  font-family: var(--ui-mono);
  font-size: 10.5px;
  color: var(--dim);
}

.dim {
  color: var(--dim);
}

code {
  font-family: var(--ui-mono);
  font-size: 0.92em;
}

/* 主按钮：新任务、发送 */
.new,
.send {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  border: 1px solid transparent;
  border-radius: var(--ui-r);
  background: var(--ui-brand);
  color: #160f2a;
  font-weight: 600;
  box-shadow: 0 3px 12px rgba(167, 139, 250, 0.22);
  transition: filter var(--ui-fast), transform var(--ui-fast), box-shadow var(--ui-fast);
}

.new:hover:enabled,
.send:hover:enabled {
  filter: brightness(1.08);
  box-shadow: 0 4px 16px rgba(167, 139, 250, 0.32);
}

.new:active:enabled,
.send:active:enabled {
  transform: translateY(1px);
}

.send:disabled {
  background: var(--raised);
  color: var(--dim);
  box-shadow: none;
}

/* 次要按钮 */
.ghost {
  margin-top: 8px;
  padding: 5px 11px;
  border: 1px solid var(--line-strong);
  border-radius: var(--ui-r-s);
  background: transparent;
  color: var(--text-2);
  font-size: 12.5px;
  transition: border-color var(--ui-fast), color var(--ui-fast), background var(--ui-fast);
}

.ghost:hover:enabled {
  border-color: var(--accent-line);
  color: var(--text);
  background: var(--accent-bg);
}

/* ---------- 左边：会话 ---------- */

.side {
  display: flex;
  flex-direction: column;
  min-height: 0;
  background: var(--side);
  border-right: 1px solid var(--line);
}

.side-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 16px 14px 10px 18px;
}

/* 窗口窄（侧栏 216px）时标题和按钮也不折行：以前「任务」被挤成两行、按钮文字竖着排 */
.side-head h2,
.head-actions > * {
  flex-shrink: 0;
  white-space: nowrap;
}

.side-head h2 {
  margin: 0;
  font-size: 17px;
  font-weight: 700;
  letter-spacing: 0.5px;
}

.side-head .new {
  padding: 5px 11px 5px 9px;
  font-size: 12.5px;
}

.head-actions {
  display: flex;
  gap: 6px;
}

.board-toggle {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 5px 10px;
  border: 1px solid var(--line);
  border-radius: var(--ui-r-s);
  background: transparent;
  color: var(--dim);
  font-size: 12.5px;
  cursor: pointer;
}

.board-toggle.on {
  border-color: var(--accent-line);
  color: var(--accent);
}

.board-toggle .dot {
  min-width: 16px;
  padding: 0 4px;
  border-radius: 999px;
  background: var(--ui-warn-soft);
  color: var(--ui-warn);
  font-size: 11px;
  text-align: center;
}

.search {
  display: flex;
  align-items: center;
  gap: 7px;
  margin: 0 12px 8px;
  padding: 0 10px;
  border: 1px solid var(--line);
  border-radius: var(--ui-r);
  background: var(--ui-sunken);
  color: var(--ui-faint);
  transition: border-color var(--ui-fast);
}

.search:focus-within {
  border-color: var(--accent-line);
  color: var(--accent);
}

.search input {
  flex: 1;
  min-width: 0;
  padding: 7px 0;
  border: none;
  background: none;
  color: var(--text);
  font: inherit;
  font-size: 13px;
  outline: none;
  box-shadow: none;
}

.search input::placeholder {
  color: var(--ui-faint);
}

.list {
  flex: 1;
  overflow-y: auto;
  padding: 0 8px 8px;
}

.group {
  padding: 12px 10px 5px;
  color: var(--ui-faint);
  font-size: 11.5px;
  font-weight: 600;
  letter-spacing: 1px;
}

.sess {
  position: relative;
  padding: 8px 34px 8px 12px;
  border-radius: var(--ui-r);
  cursor: pointer;
  transition: background var(--ui-fast);
}

.sess:hover,
.sess.menu {
  background: var(--card);
}

.sess.on {
  background: var(--accent-bg);
}

.sess.on::before {
  content: '';
  position: absolute;
  left: 0;
  top: 9px;
  bottom: 9px;
  width: 3px;
  border-radius: 0 3px 3px 0;
  background: var(--accent);
}

.sess .title {
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  font-size: 13.5px;
}

.sess.on .title {
  color: var(--ui-accent-hi);
  font-weight: 600;
}

.sess .meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 2px 4px;
  margin-top: 3px;
  overflow: hidden;
  color: var(--dim);
  font-size: 11.5px;
  white-space: nowrap;
}

.sess .meta > span:first-child {
  overflow: hidden;
  text-overflow: ellipsis;
}

.meta-plan {
  color: var(--ui-accent-hi);
}

.meta-wait {
  flex: none;
  padding: 0 6px;
  border-radius: 999px;
  background: var(--ui-warn-soft);
  color: var(--warn);
  font-size: 11px;
  line-height: 17px;
}

.meta-branch {
  display: inline-flex;
  color: var(--ui-cyan);
}

.sess .dot {
  color: var(--ui-faint);
}

.more {
  position: absolute;
  top: 50%;
  right: 6px;
  display: grid;
  place-items: center;
  width: 24px;
  height: 24px;
  padding: 0;
  border: none;
  border-radius: var(--ui-r-s);
  background: none;
  color: var(--dim);
  opacity: 0;
  transform: translateY(-50%);
  transition: opacity var(--ui-fast), background var(--ui-fast);
}

.sess:hover .more,
.sess.menu .more,
.more:focus-visible {
  opacity: 1;
}

.more:hover {
  background: var(--raised);
  color: var(--text);
}

.menu-pop {
  position: absolute;
  top: calc(100% - 4px);
  right: 6px;
  z-index: 20;
  display: flex;
  flex-direction: column;
  min-width: 120px;
  padding: 4px;
  border: 1px solid var(--line-strong);
  border-radius: var(--ui-r);
  background: var(--raised);
  box-shadow: var(--ui-shadow);
}

.menu-pop button {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 10px;
  border: none;
  border-radius: var(--ui-r-s);
  background: none;
  font-size: 13px;
  text-align: left;
}

.menu-pop button:hover {
  background: var(--accent-bg);
}

.menu-pop .danger {
  color: var(--ui-bad);
}

.menu-pop .danger:hover {
  background: var(--ui-bad-soft);
}

.rename {
  box-sizing: border-box;
  width: calc(100% + 22px);
  padding: 5px 8px;
  border: 1px solid var(--accent);
  border-radius: var(--ui-r-s);
  background: var(--ui-sunken);
  color: var(--text);
  font: inherit;
  font-size: 13.5px;
  outline: none;
}

/* 会话列表里「她在做」的小圆点。名字别叫 live：流式输出的文字以前也用了 live，被这条规则压成 7px 宽，一个字一行 */
.live-dot {
  display: inline-block;
  width: 7px;
  height: 7px;
  margin-right: 7px;
  border-radius: 50%;
  background: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-bg);
  vertical-align: 1px;
  animation: pulse 1.2s infinite;
}

@keyframes pulse {
  50% {
    opacity: 0.35;
  }
}

.none {
  padding: 24px 12px;
  color: var(--dim);
  font-size: 13px;
  text-align: center;
}

.side-foot {
  padding: 10px 18px;
  border-top: 1px solid var(--line);
  font-size: 12px;
  color: var(--dim);
}

.side-foot code {
  padding: 1px 6px;
  border-radius: var(--ui-r-xs);
  background: var(--card);
  color: var(--ui-accent-hi);
}

/* ---------- 中间 ---------- */

.main {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
}

.top {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 52px;
  padding: 0 18px;
  border-bottom: 1px solid var(--line);
  background: var(--bg);
}

.proj {
  display: flex;
  align-items: baseline;
  gap: 10px;
  flex: 1;
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
}

.proj b {
  font-size: 15px;
}

.proj .path {
  overflow: hidden;
  text-overflow: ellipsis;
  color: var(--ui-faint);
  font-family: var(--ui-mono);
  font-size: 11.5px;
}

.mode {
  padding: 4px 8px;
  border: 1px solid var(--line);
  border-radius: 999px;
  background: var(--card);
  color: var(--text-2);
  font: inherit;
  font-size: 12.5px;
  cursor: pointer;
}

.mode:hover {
  border-color: var(--accent-line);
}

.model {
  max-width: 180px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--dim);
  font-family: var(--ui-mono);
  font-size: 11.5px;
  cursor: pointer;
}

.model:hover {
  color: var(--accent);
}

.tabs {
  display: flex;
  gap: 2px;
  padding: 2px;
  border: 1px solid var(--line);
  border-radius: var(--ui-r);
  background: var(--ui-sunken);
}

.tabs button {
  padding: 3px 12px;
  border: none;
  border-radius: 7px;
  background: transparent;
  color: var(--dim);
  font-size: 12.5px;
  transition: background var(--ui-fast), color var(--ui-fast);
}

.tabs button:hover {
  color: var(--text);
}

.tabs button.on {
  background: var(--raised);
  color: var(--text);
  box-shadow: var(--ui-shadow-s);
}

.scroll {
  flex: 1;
  overflow-y: auto;
}

.transcript {
  max-width: 880px;
  margin: 0 auto;
  padding: 20px 28px 36px;
}

/* 你说的话：靠右的浅紫气泡 */
.user {
  width: fit-content;
  max-width: 82%;
  margin: 22px 0 10px auto;
  padding: 9px 14px;
  border: 1px solid var(--accent-line);
  border-radius: 14px 14px 4px 14px;
  background: var(--ui-brand-soft);
  white-space: pre-wrap;
  line-height: 1.6;
}

.user.review {
  display: flex;
  align-items: center;
  gap: 8px;
  border-color: var(--ui-cyan-line);
  background: var(--ui-cyan-soft);
}

.stage-line {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 22px 0 8px;
  padding: 6px 0;
  border-top: 1px dashed var(--accent-line);
  color: var(--ui-accent-hi);
  font-size: 13px;
  font-weight: 600;
}

.chip.on {
  border-color: var(--accent-line);
  background: var(--accent-bg);
  color: var(--ui-accent-hi);
}

.plan-side-goal {
  margin: 0 0 8px !important;
  color: var(--text-2);
}

.plan-steps {
  margin: 0;
  padding: 0;
  list-style: none;
}

.info .plan-steps li {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 0;
  white-space: normal;
  font-size: 12.5px;
  color: var(--text-2);
}

.plan-steps i {
  flex: none;
  width: 8px;
  height: 8px;
  border: 1.5px solid var(--line-strong);
  border-radius: 50%;
}

.plan-steps .running {
  color: var(--ui-accent-hi);
}

.plan-steps .running i {
  border-color: var(--accent);
  background: var(--accent);
  animation: pulse 1.2s infinite;
}

.plan-steps .done {
  color: var(--dim);
}

.plan-steps .done i {
  border-color: var(--ok);
  background: var(--ok);
}

.plan-steps .blocked {
  color: var(--warn);
}

.plan-steps .blocked i {
  border-color: var(--warn);
}

.plan-ok {
  margin-top: 8px !important;
  color: var(--ok);
}

.plan-paused {
  margin-top: 8px !important;
  color: var(--warn);
}

.plan-continue {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.user.review span {
  padding: 0 6px;
  border: 1px solid var(--ui-cyan-line);
  border-radius: 999px;
  color: var(--ui-cyan);
  font-size: 11px;
}

.md {
  margin: 8px 0;
  line-height: 1.75;
}

.md :deep(p) {
  margin: 6px 0;
}

.md :deep(h4) {
  margin: 14px 0 4px;
  font-size: 15px;
}

.md :deep(ul),
.md :deep(ol) {
  margin: 4px 0;
  padding-left: 22px;
}

.md :deep(code) {
  padding: 1px 5px;
  border-radius: var(--ui-r-xs);
  background: var(--code);
  font-family: var(--ui-mono);
  font-size: 12.5px;
  color: var(--ui-code-inline);
}

.md :deep(pre) {
  margin: 10px 0;
  padding: 12px 14px;
  border: 1px solid var(--line);
  border-radius: var(--ui-r);
  background: var(--code);
  overflow-x: auto;
}

.md :deep(pre code) {
  padding: 0;
  background: none;
  color: var(--ui-code-text);
}

.md :deep(blockquote) {
  margin: 6px 0;
  padding-left: 12px;
  border-left: 3px solid var(--accent-line);
  color: var(--dim);
}

.md :deep(.md-link) {
  color: var(--ui-cyan);
}

/* 一轮结束那一行 */
.end {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
  margin: 14px 0 6px;
  padding: 8px 12px;
  border: 1px solid var(--ui-ok-line);
  border-radius: var(--ui-r);
  background: var(--ui-ok-soft);
  color: var(--ok);
  font-size: 13px;
}

.end.error {
  border-color: var(--ui-bad-line);
  background: var(--ui-bad-soft);
  color: var(--bad);
}

.end:not(.done):not(.error) {
  border-color: var(--ui-warn-line);
  background: var(--ui-warn-soft);
  color: var(--warn);
}

.end .stats {
  color: var(--dim);
  font-family: var(--ui-mono);
  font-size: 11.5px;
}

.undo-button {
  margin: 0 0 0 auto;
}

.undo-dialog {
  box-sizing: border-box;
  width: min(520px, 92vw);
  max-height: 86vh;
  overflow: auto;
  padding: 20px 24px;
  border: 1px solid var(--line-strong);
  border-radius: var(--ui-r-l);
  background: var(--raised);
  color: var(--text);
  box-shadow: var(--ui-shadow);
  overflow-wrap: anywhere;
  line-height: 1.6;
}

.undo-dialog::backdrop {
  background: var(--ui-overlay);
  backdrop-filter: blur(2px);
}

.undo-dialog h2 {
  margin: 0 0 12px;
  font-size: 17px;
}

.undo-dialog p {
  margin: 6px 0;
}

.undo-actions {
  display: flex;
  justify-content: flex-end;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 16px;
}

.undo-actions .ghost {
  margin: 0;
  padding: 6px 14px;
}

.undo-actions .undo-confirm {
  border-color: var(--ui-warn-line);
  color: var(--warn);
}

.undo-actions .danger-btn {
  border-color: var(--ui-bad-line);
  background: var(--ui-bad-soft);
  color: var(--ui-bad-text);
}

.undo-actions .danger-btn:hover:enabled {
  border-color: var(--bad);
  background: rgba(248, 113, 113, 0.18);
  color: #fff;
}

.note {
  margin: 10px 0;
  color: var(--dim);
  font-size: 12.5px;
  text-align: center;
}

.working {
  display: flex;
  align-items: center;
  gap: 4px;
  margin: 12px 0;
  color: var(--dim);
  font-size: 13px;
}

.working i {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--accent);
  animation: pulse 1s infinite;
}

.working i:nth-child(2) {
  animation-delay: 0.2s;
}

.working i:nth-child(3) {
  animation-delay: 0.4s;
  margin-right: 6px;
}

.working span {
  margin-left: 8px;
  font-size: 12px;
}

.hello {
  margin: 48px 0;
  color: var(--text-2);
  text-align: center;
  line-height: 1.7;
}

.hello b {
  color: var(--text);
}

.hello.big {
  margin-top: 20vh;
}

.hello-mark {
  display: inline-grid;
  place-items: center;
  width: 56px;
  height: 56px;
  margin-bottom: 10px;
  border-radius: 16px;
  background: var(--ui-brand-soft);
  border: 1px solid var(--accent-line);
  color: var(--ui-accent-hi);
  font-family: var(--ui-mono);
  font-size: 20px;
  font-weight: 700;
}

.hello .new {
  margin-top: 14px;
  padding: 8px 18px 8px 14px;
}

.error {
  margin: 0 18px 8px;
  padding: 8px 12px;
  border: 1px solid var(--ui-bad-line);
  border-radius: var(--ui-r);
  background: var(--ui-bad-soft);
  color: var(--ui-bad-text);
  font-size: 13px;
  cursor: pointer;
}

/* 输入框：一个圆角盒子，下面一条工具栏 */
.composer {
  padding: 4px 22px 16px;
}

/* 输入「/」时的命令菜单：贴在输入框上方 */
.box {
  position: relative;
}

.cmd-menu {
  position: absolute;
  left: 0;
  right: 0;
  bottom: calc(100% + 6px);
  z-index: 5;
  padding: 6px;
  border: 1px solid var(--line-strong);
  border-radius: var(--ui-r);
  background: var(--card);
  box-shadow: var(--ui-shadow);
}

.cmd-menu button {
  display: flex;
  align-items: baseline;
  gap: 10px;
  width: 100%;
  padding: 7px 10px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: inherit;
  text-align: left;
  cursor: pointer;
}

.cmd-menu button.on {
  background: var(--ui-brand-soft);
}

.cmd-menu b {
  flex: none;
  font-family: var(--ui-mono);
}

.cmd-menu .desc {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  color: var(--dim);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cmd-menu .src {
  flex: none;
  color: var(--dim);
  font-size: 11px;
}

.cmd-hint,
.cmd-keys {
  padding: 6px 10px 2px;
  color: var(--dim);
  font-size: 12px;
  line-height: 1.6;
}

.cmd-keys {
  text-align: right;
}

/* 自定义命令的那句话：/发布 v1.2，可以展开看发了什么 */
.user.command .command-head {
  display: flex;
  align-items: center;
  gap: 8px;
}

.user.command .link {
  padding: 0;
  border: none;
  background: none;
  color: var(--accent);
  font-size: 12px;
  cursor: pointer;
}

.command-body {
  max-height: 260px;
  margin: 8px 0 0;
  overflow: auto;
  color: var(--dim);
  font-size: 12px;
  white-space: pre-wrap;
}

.box {
  max-width: 880px;
  margin: 0 auto;
  border: 1px solid var(--line-strong);
  border-radius: var(--ui-r-l);
  background: var(--card);
  box-shadow: var(--ui-shadow-s);
  transition: border-color var(--ui-fast), box-shadow var(--ui-fast);
}

.box:focus-within {
  border-color: var(--accent-line);
  box-shadow: 0 0 0 3px var(--accent-bg), var(--ui-shadow-s);
}

.box textarea {
  box-sizing: border-box;
  display: block;
  width: 100%;
  resize: none;
  padding: 12px 14px 4px;
  border: none;
  background: transparent;
  color: var(--text);
  font: inherit;
  line-height: 1.6;
  outline: none;
  box-shadow: none;
}

.box textarea::placeholder {
  color: var(--ui-faint);
}

.bar {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 8px 8px 10px;
}

.chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border: 1px solid var(--line);
  border-radius: 999px;
  background: var(--ui-sunken);
  color: var(--text-2);
  font-size: 12.5px;
  transition: border-color var(--ui-fast), color var(--ui-fast), background var(--ui-fast);
}

.chip:hover:enabled {
  border-color: var(--ui-cyan-line);
  background: var(--ui-cyan-soft);
  color: var(--ui-cyan);
}

.keys {
  flex: 1;
  color: var(--ui-faint);
  font-size: 11.5px;
  text-align: right;
}

.send,
.stop {
  padding: 6px 14px;
  font-size: 13px;
}

.stop {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: 1px solid var(--ui-warn-line);
  border-radius: var(--ui-r);
  background: var(--ui-warn-soft);
  color: var(--warn);
}

/* ---------- 右边 ---------- */

.info {
  overflow-y: auto;
  padding: 4px 16px 16px;
  background: var(--side);
  border-left: 1px solid var(--line);
}

.info section {
  padding: 14px 0;
  border-bottom: 1px solid var(--line);
}

.info section:last-child {
  border-bottom: none;
}

.info h3 {
  margin: 0 0 8px;
  color: var(--ui-faint);
  font-size: 11.5px;
  font-weight: 600;
  letter-spacing: 1px;
}

.info p {
  margin: 0;
  font-size: 12.5px;
  line-height: 1.6;
}

.info ul {
  margin: 0;
  padding: 0;
  list-style: none;
}

.info li {
  padding: 3px 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  font-size: 13px;
}

.todos .completed {
  color: var(--dim);
  text-decoration: line-through;
  text-decoration-color: var(--ui-faint);
}

.todos .in_progress {
  color: var(--ui-accent-hi);
}

.files li {
  font-family: var(--ui-mono);
  font-size: 12px;
  color: var(--text-2);
}

/* 项目记忆：一条一行，可以换行；左边小点分她记的（紫）和你加的（青） */
.notes li {
  position: relative;
  display: flex;
  align-items: flex-start;
  gap: 6px;
  padding: 5px 0 5px 12px;
  overflow: visible;
  white-space: normal;
  font-size: 12.5px;
  line-height: 1.5;
  color: var(--text-2);
}

.notes li::before {
  content: '';
  position: absolute;
  left: 0;
  top: 11px;
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--accent);
}

.notes li.user::before {
  background: var(--ui-cyan);
}

.notes li span {
  flex: 1;
  min-width: 0;
  overflow-wrap: anywhere;
}

.notes .x {
  display: grid;
  place-items: center;
  flex: none;
  width: 20px;
  height: 20px;
  padding: 0;
  border: none;
  border-radius: var(--ui-r-xs);
  background: none;
  color: var(--ui-faint);
  opacity: 0;
  transition: opacity var(--ui-fast), color var(--ui-fast);
}

.notes li:hover .x,
.notes .x:focus-visible {
  opacity: 1;
}

.notes .x:hover {
  color: var(--bad);
}

.note-add {
  display: flex;
  gap: 6px;
  margin-top: 8px;
}

.note-add input {
  flex: 1;
  min-width: 0;
  padding: 5px 8px;
  border: 1px solid var(--line);
  border-radius: var(--ui-r-s);
  background: var(--ui-sunken);
  color: var(--text);
  font: inherit;
  font-size: 12.5px;
  outline: none;
}

.note-add input:focus {
  border-color: var(--accent-line);
}

.note-add button {
  padding: 4px 10px;
  border: 1px solid var(--line-strong);
  border-radius: var(--ui-r-s);
  background: transparent;
  color: var(--text-2);
  font-size: 12.5px;
}

.note-add button:hover:enabled {
  border-color: var(--accent-line);
  background: var(--accent-bg);
  color: var(--text);
}

.rate b {
  font-size: 20px;
  color: var(--ok);
}

.bar-mini,
.info .bar {
  height: 5px;
  margin: 6px 0;
  padding: 0;
  border-radius: 3px;
  background: var(--line);
  overflow: hidden;
}

/* ---------- 在新分支上做 ---------- */

.branch-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 9px;
  border: 1px solid var(--ui-cyan-line);
  border-radius: 999px;
  background: var(--ui-cyan-soft);
  color: var(--ui-cyan);
  font-size: 11.5px;
  font-family: var(--ui-mono);
  white-space: nowrap;
}

.branch-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}

.branch-actions .ghost {
  margin: 0;
}

.merge-btn {
  padding: 5px 12px;
  border: 1px solid var(--ui-ok-line);
  border-radius: var(--ui-r-s);
  background: var(--ui-ok-soft);
  color: var(--ok);
}

.merge {
  margin: 12px 0;
  padding: 12px 14px;
  border: 1px solid var(--ui-warn-line);
  border-radius: var(--ui-r);
  background: var(--ui-warn-soft);
}

.merge.ok {
  border-color: var(--ui-ok-line);
  background: var(--ui-ok-soft);
}

.merge-head {
  font-weight: 600;
  color: var(--warn);
}

.merge.ok .merge-head {
  color: var(--ok);
}

.merge-tests {
  margin: 6px 0;
  padding: 0;
  list-style: none;
}

.merge-tests .bad {
  color: var(--bad);
}

.merge-tests pre {
  margin: 4px 0;
  padding: 8px 10px;
  max-height: 240px;
  overflow: auto;
  border-radius: var(--ui-r-s);
  background: var(--code);
  color: var(--ui-code-text);
  font-family: var(--ui-mono);
  font-size: 12px;
  white-space: pre-wrap;
}

.merge-actions {
  margin-top: 6px;
}

/* ---------- 新任务：选项目 ---------- */

.overlay {
  position: fixed;
  inset: 0;
  z-index: 10;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--ui-overlay);
  backdrop-filter: blur(3px);
}

.picker {
  box-sizing: border-box;
  width: min(640px, 92vw);
  max-height: 86vh;
  overflow-y: auto;
  padding: 22px 24px;
  border: 1px solid var(--line-strong);
  border-radius: var(--ui-r-l);
  background: var(--raised);
  box-shadow: var(--ui-shadow);
}

.picker h2 {
  margin: 0 0 14px;
  font-size: 18px;
}

.picker > .dim {
  font-size: 12.5px;
}

.choices {
  display: grid;
  gap: 6px;
  margin-top: 14px;
  padding-top: 14px;
  border-top: 1px solid var(--line);
}

.choices-title {
  margin: 0 0 4px;
  font-size: 13px;
  font-weight: 600;
  color: var(--ui-text-2);
}

.task-kinds {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 0 0 14px;
  padding: 0;
  border: 0;
}
.task-kinds legend { margin-bottom: 8px; color: var(--text-2); font-size: 13px; }
.task-kinds label { display: flex; align-items: center; gap: 8px; padding: 10px 12px; border: 1px solid var(--line); border-radius: var(--ui-r); cursor: pointer; }
.task-kinds label.on { border-color: var(--accent); background: var(--accent-bg); }
.task-kinds input { accent-color: var(--accent); }
.task-kinds label:focus-within, .modes label:focus-within { outline: 2px solid var(--accent); outline-offset: 2px; }
.computer-start { margin-right: 12px; }
.modes.computer { grid-template-columns: repeat(2, 1fr); }

.choice {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  padding: 9px 12px;
  border: 1px solid var(--line);
  border-radius: var(--ui-r);
  background: var(--card);
  text-align: left;
  transition: border-color var(--ui-fast), background var(--ui-fast);
}

.choice:hover {
  border-color: var(--accent-line);
  background: var(--accent-bg);
}

.choice span {
  margin-top: 2px;
  font-family: var(--ui-mono);
  font-size: 11.5px;
  color: var(--dim);
  word-break: break-all;
}

.choice.other {
  align-items: center;
  border-style: dashed;
  color: var(--accent);
}

.isolate {
  display: flex;
  gap: 10px;
  align-items: flex-start;
  margin-top: 14px;
  padding: 10px 12px;
  border: 1px solid var(--line);
  border-radius: var(--ui-r);
  cursor: pointer;
  transition: border-color var(--ui-fast), background var(--ui-fast);
}

.isolate.on {
  border-color: var(--ui-cyan-line);
  background: var(--ui-cyan-soft);
}

.isolate input {
  accent-color: var(--ui-cyan);
}

.isolate span {
  font-size: 12.5px;
  color: var(--dim);
}

.isolate b {
  display: block;
  color: var(--text);
  font-size: 14px;
}

.modes {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 6px;
  margin: 14px 0 8px;
}

.modes label {
  display: flex;
  flex-direction: column;
  padding: 9px 11px;
  border: 1px solid var(--line);
  border-radius: var(--ui-r);
  cursor: pointer;
  transition: border-color var(--ui-fast), background var(--ui-fast);
}

.modes label:hover {
  border-color: var(--line-strong);
}

.modes label.on {
  border-color: var(--accent);
  background: var(--accent-bg);
}

.modes input {
  position: absolute;
  width: 1px;
  height: 1px;
  opacity: 0;
}

.modes span {
  margin-top: 2px;
  font-size: 12px;
  color: var(--dim);
}

.new-budget {
  margin: 14px 0;
}

.new-budget label {
  display: block;
  margin-bottom: 6px;
  font-size: 13px;
}

.new-budget input {
  box-sizing: border-box;
  width: min(200px, 100%);
  padding: 7px 10px;
  border: 1px solid var(--line-strong);
  border-radius: var(--ui-r-s);
  background: var(--ui-sunken);
  color: var(--text);
  font-family: var(--ui-mono);
}

.new-budget .dim {
  margin: 6px 0 0;
  font-size: 12px;
}

.budget-error {
  margin: 6px 0;
  color: var(--bad);
}
</style>
