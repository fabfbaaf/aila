<script setup lang="ts">
/**
 * 工作窗口：在这里给她编程任务。
 * 左边是会话（终端里用 aila 开的也在这里），中间是她做事的过程，右边是这次任务的待办、改过的文件和用量。
 */
import { computed, nextTick, onActivated, onBeforeUnmount, onDeactivated, onMounted, reactive, ref, shallowRef } from 'vue'
import { MODE_HINTS, MODE_LABELS, type AgentLedger, type AgentMode, type AgentSessionSummary } from '../../../shared/agent'
import { Transcript, endStats, endText, type EndItem, type ToolItem, type UndoPreview } from './fold'
import { renderMarkdown } from './markdown'
import { createAgentSource, type ProjectChoice } from './source'
import { buildTrace } from './trace'
import LedgerPanel from './LedgerPanel.vue'
import ToolCard from './ToolCard.vue'
import TraceView from './TraceView.vue'
import UndoResult from './UndoResult.vue'
import BudgetControls from './BudgetControls.vue'
import { parseBudget, validBudget } from '../../../shared/budget'

const source = createAgentSource()
const sessions = ref<AgentSessionSummary[]>([])
const current = ref<AgentSessionSummary | null>(null)
const t = shallowRef(reactive(new Transcript()))
const text = ref('')
const error = ref('')
const picking = ref(false)
const projects = ref<ProjectChoice[]>([])
const newMode = ref<AgentMode>('ask')
/** 新任务在新分支上做（git worktree，测试通过才合并回来） */
const newIsolate = ref(false)
const newBudget = ref('')
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
  if (!m.keySet) return '假模型（还没填密钥）'
  return m.coder ? m.coder : `${m.chat}（和对话一样）`
})
const choices = computed<ProjectChoice[]>(() => {
  const seen = new Set<string>()
  const out: ProjectChoice[] = []
  for (const p of [...sessions.value.map((s) => ({ name: basename(s.cwd), path: s.cwd })), ...projects.value]) {
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
async function refreshList(): Promise<void> {
  const request = ++listRequest
  try {
    const result = await source.list()
    if (request !== listRequest) return
    sessions.value = result
    const active = current.value
    const latest = active && result.find((session) => session.id === active.id)
    if (active && latest && latest.events > t.value.lastSeq) {
      active.cost = latest.cost ?? null
      t.value.budget = validBudget(latest.budget) ? latest.budget : null
    }
  } catch (err) {
    if (request === listRequest) error.value = (err as Error).message
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

async function startNew(path: string): Promise<void> {
  error.value = ''
  newBudgetError.value = ''
  let budget: number | null
  try { budget = parseBudget(newBudget.value) }
  catch (err) { newBudgetError.value = (err as Error).message; return }
  try {
    const s = await source.create(path, newMode.value, newIsolate.value, budget)
    await refreshList()
    await open(s.id)
  } catch (err) {
    error.value = (err as Error).message
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

// ---------- 说话、确认、打断 ----------

function send(): void {
  const msg = text.value.trim()
  if (!msg || !current.value || undoBusy.value || opening.value || undoPreview.value) return
  if (running.value) {
    error.value = '她还在做事：等她做完，或者先按 Esc 打断。'
    return
  }
  error.value = ''
  text.value = ''
  streaming.add(current.value.id)
  source.send(current.value.id, msg)
  void nextTick(scrollToBottom)
}

async function answer(item: ToolItem, approved: boolean, remember: boolean): Promise<void> {
  if (!item.confirm || item.confirm.result) return
  if (!(await source.confirm(item.confirm.id, approved, remember))) error.value = '这个确认已经结束了（超时或者被打断）。'
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
  merging.value = runTests ? '正在跑测试……' : '正在合并……'
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
  if (!window.confirm(`放弃分支 ${t.value.branch}？她在分支上还没合并的改动会删掉，你的代码保持原样。`)) return
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
    error.value = '最近一轮已经变了，重新查看再撤销。'
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
    if (e.type === 'turn_end') void refreshList()
    return
  }
  const near = nearBottom()
  t.value.apply(e)
  if (e.type === 'user' || e.type === 'undo' || e.type === 'branch_closed') closeUndoPreview()
  if (e.type === 'user' || e.type === 'usage' || e.type === 'turn_end' || e.type === 'undo' || e.type === 'budget') void refreshList()
  if (['usage', 'turn_end', 'compaction', 'tool', 'undo'].includes(e.type)) loadLedger()
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

function onComposerKey(e: KeyboardEvent): void {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault()
    send()
  }
}

function onKey(e: KeyboardEvent): void {
  if (undoDialog.value?.open) return // 对话框里的 Esc 只取消撤销预览
  const typing = e.target instanceof HTMLTextAreaElement && text.value.length > 0
  const p = pending.value
  if (p?.confirm && !typing) {
    if (e.key === '1') return void (e.preventDefault(), answer(p, true, false))
    if (e.key === '2' && p.confirm.canRemember) return void (e.preventDefault(), answer(p, true, true))
    if (e.key === 'Escape') return void answer(p, false, false)
  }
  if (e.key === 'Escape') {
    if (picking.value && current.value) picking.value = false
    else void interrupt()
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
  if (mins < 1) return '刚刚'
  if (mins < 60) return `${Math.floor(mins)} 分钟前`
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
  // 轨迹里正在做的那一轮每秒往右长一点（只在看轨迹、而且她在做事的时候）
  clock = window.setInterval(() => {
    if (running.value && view.value === 'trace') now.value = Date.now() / 1000
  }, 1000)
  if (import.meta.env.DEV) (window as unknown as Record<string, unknown>).__agent = { t, open, source }
  await refreshList()
  void source.projects().then((p) => (projects.value = p))
  void source.models().then((m) => (models.value = m))
  const focus = await source.takeFocus()
  if (focus) await open(focus)
  else if (sessions.value.length) await open(sessions.value[0].id)
  else picking.value = true
})

// 在主窗口里切到别的页面时，任务页还留着（KeepAlive）：这时不能再听 1 / 2 / Esc，
// 不然在设置里打个「1」就替你同意了
onActivated(() => window.addEventListener('keydown', onKey))
onDeactivated(() => {
  window.removeEventListener('keydown', onKey)
  closeUndoPreview()
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKey)
  clearInterval(clock)
  clearTimeout(ledgerTimer)
})
</script>

<template>
  <div class="app" :class="{ bare: !current }">
    <aside class="side">
      <div class="brand">艾拉 <span>编程</span></div>
      <button class="new" @click="picking = true">＋ 新任务</button>
      <div class="list">
        <div
          v-for="s in sessions"
          :key="s.id"
          class="sess"
          :class="{ on: s.id === current?.id }"
          :title="s.cwd"
          @click="open(s.id)"
        >
          <div class="title"><i v-if="s.running" class="live"></i>{{ s.title }}</div>
          <div class="meta">{{ basename(s.cwd) }} · {{ when(s.updated) }}</div>
        </div>
        <div v-if="!sessions.length" class="none">还没有任务</div>
      </div>
      <div class="side-foot">终端里也能用：<code>aila</code></div>
    </aside>

    <main class="main">
      <header v-if="current" class="top">
        <div class="proj">
          <b>{{ basename(current.cwd) }}</b>
          <span class="path">{{ current.cwd }}</span>
        </div>
        <span v-if="t.branch" class="branch-badge" title="她在这个分支的临时目录里改，你的代码一个字都没动">⑂ {{ t.branch }}</span>
        <select class="mode" :value="t.mode" :title="MODE_HINTS[t.mode]" @change="setMode(($event.target as HTMLSelectElement).value as AgentMode)">
          <option v-for="(label, m) in MODE_LABELS" :key="m" :value="m">{{ label }}</option>
        </select>
        <span class="model" title="编程用的模型：在「设置 → 对话」里改" @click="source.openSettings()">{{ modelLabel }}</span>
        <div class="tabs">
          <button :class="{ on: view === 'chat' }" @click="view = 'chat'">过程</button>
          <button :class="{ on: view === 'trace' }" title="按时间看每一步花了多久" @click="view = 'trace'">轨迹</button>
        </div>
      </header>
      <BudgetControls v-if="current" class="task-budget" :sid="current.id" :cost="current.cost" :budget="t.budget" :change="source.setBudget" @changed="changedBudget" />

      <div v-if="current && view === 'trace'" class="scroll">
        <TraceView :turns="trace" />
      </div>
      <div v-else ref="scroller" class="scroll">
        <div v-if="current" class="transcript">
          <div v-if="!t.items.length" class="hello">
            <p>在 <b>{{ basename(current.cwd) }}</b> 里，要她做什么？</p>
            <p class="dim">比如「这个项目是做什么的」「给购物车加一个打折函数，写好测试并跑通」「看看为什么 npm test 失败」</p>
          </div>
          <template v-for="item in t.items" :key="item.key">
            <div v-if="item.kind === 'user'" class="user">{{ item.text }}</div>
            <!-- eslint-disable-next-line vue/no-v-html：renderMarkdown 先整段转义，只插入固定的几种标签 -->
            <div v-else-if="item.kind === 'text'" class="md" :class="{ live: item.live }" v-html="renderMarkdown(item.text)"></div>
            <ToolCard v-else-if="item.kind === 'tool'" :item="item" @answer="(a: boolean, r: boolean) => answer(item, a, r)" />
            <div v-else-if="item.kind === 'merge'" class="merge" :class="{ ok: item.merged }">
              <div class="merge-head">{{ item.merged ? '✓ 合并了' : '■ 没有合并' }}</div>
              <div>{{ item.reason }}</div>
              <ul v-if="item.tests.length" class="merge-tests">
                <li v-for="(tr, i) in item.tests" :key="i" :class="{ bad: !tr.ok }">
                  {{ tr.ok ? '✓' : '✗' }} {{ tr.label }} · {{ tr.seconds }} 秒
                  <pre v-if="!tr.ok && tr.tail">{{ tr.tail }}</pre>
                </li>
              </ul>
              <div v-if="item.files.length" class="dim">文件：{{ item.files.join('、') }}</div>
              <div v-if="item.noTests && t.branch" class="merge-actions">
                <button class="ghost" :disabled="!!merging" @click="mergeNow(false)">不跑测试，直接合并</button>
              </div>
            </div>
            <div v-else-if="item.kind === 'end'" class="end" :class="item.reason">
              <span>{{ endMark[item.reason] ?? '■' }} {{ endText(item) }}</span>
              <span class="stats">{{ endStats(item) }}</span>
              <button v-if="undoTarget?.key === item.key" class="ghost undo-button" :disabled="undoBusy" @click="previewUndo(item)">
                {{ undoBusy ? '正在撤销…' : '撤销这一轮' }}
              </button>
            </div>
            <UndoResult v-else-if="item.kind === 'undo'" :result="item.result" />
            <div v-else class="note">{{ item.text }}</div>
          </template>
          <div v-if="running && !pending" class="working"><i></i><i></i><i></i>她在做……<span>Esc 打断</span></div>
        </div>
        <div v-else class="hello big">
          <p>给她一个编程任务：她会读代码、改文件、跑命令和测试，直到做完。</p>
          <button class="new" @click="picking = true">＋ 新任务</button>
        </div>
      </div>

      <div v-if="error" class="error" @click="error = ''">{{ error }}</div>

      <footer v-if="current" class="composer">
        <textarea
          ref="input"
          v-model="text"
          rows="3"
          :disabled="t.closed"
          :placeholder="
            t.closed
              ? '这个会话的分支已经放弃了，点左上角「新任务」开一个新的'
              : running
                ? '她在做事……想好的下一句可以先写着'
                : '要她做什么？Enter 发送，Shift+Enter 换行'
          "
          @keydown="onComposerKey"
        ></textarea>
        <button v-if="running" class="stop" @click="interrupt">停下 <kbd>Esc</kbd></button>
        <button v-else class="send" :disabled="!text.trim() || undoBusy || opening" @click="send">发送</button>
      </footer>
    </main>

    <aside v-if="current" class="info">
      <section v-if="t.branch">
        <h3>分支</h3>
        <p class="dim">她在 <code>{{ t.branch }}</code> 上改，你的代码一个字都没动。觉得可以了就合并：先跑项目的测试，全过、而且你这期间没改过同样的文件，才写回你的代码（不替你提交）。</p>
        <div class="branch-actions">
          <button class="merge-btn" :disabled="running || !!merging || undoBusy" @click="mergeNow(true)">{{ merging || '跑测试并合并' }}</button>
          <button class="ghost" :disabled="running || !!merging || undoBusy" @click="discardNow">放弃分支</button>
        </div>
      </section>
      <section>
        <h3>待办</h3>
        <ul v-if="t.todos.length" class="todos">
          <li v-for="(item, i) in t.todos" :key="i" :class="item.status">
            {{ item.status === 'completed' ? '☑' : item.status === 'in_progress' ? '◐' : '☐' }} {{ item.content }}
          </li>
        </ul>
        <p v-else class="dim">步骤多的任务她会先列出来</p>
      </section>
      <section>
        <h3>改过的文件</h3>
        <ul v-if="files.length" class="files">
          <li v-for="[path, n] in files" :key="path" :title="path">{{ path }}<span v-if="n > 1" class="dim"> ×{{ n }}</span></li>
        </ul>
        <p v-else class="dim">还没有</p>
      </section>
      <section>
        <h3>上下文账本</h3>
        <LedgerPanel :ledger="ledger" :steps="t.steps" :totals="usage" />
        <button class="ghost" :disabled="running || undoBusy" title="用一份交接说明代替前面的对话（快满时会自动做）" @click="compactNow">压缩上下文</button>
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
        <h2 id="undo-heading">撤销最近一轮？</h2>
        <p id="undo-summary">以下文件会退回到这一轮开始前。你之后改过的文件会跳过。</p>
        <ul>
          <li v-for="file in undoPreview.plan.files" :key="file.path">
            <code>{{ file.path }}</code> · {{ file.created ? '删除这一轮新建的文件' : '恢复原内容' }}
          </li>
        </ul>
        <p v-if="undoPreview.plan.missing.length" class="dim">
          未留底的文件会跳过：{{ undoPreview.plan.missing.join('、') }}
        </p>
        <p v-if="undoPreview.branch" class="dim">
          只处理分支 <code>{{ undoPreview.branch }}</code> 的临时目录，已经合并到项目里的改动需要另外处理。
        </p>
        <p v-if="undoPreview.plan.commands" class="dim">这一轮还跑过命令，命令做的事无法撤销。</p>
        <div class="undo-actions">
          <button type="button" class="ghost" autofocus @click="closeUndoPreview">取消</button>
          <button type="button" class="ghost undo-confirm" @click="undoNow">确认撤销</button>
        </div>
      </template>
    </dialog>

    <div v-if="picking" class="overlay" @click.self="current && (picking = false)">
      <div class="picker">
        <h2>在哪个项目里做？</h2>
        <div class="choices">
          <button v-for="p in choices" :key="p.path" class="choice" :title="p.path" @click="startNew(p.path)">
            <b>{{ p.name }}</b><span>{{ p.path }}</span>
          </button>
          <button class="choice other" @click="pickOther">选择其他文件夹…</button>
        </div>
        <label class="isolate" :class="{ on: newIsolate }">
          <input v-model="newIsolate" type="checkbox" />
          <span>
            <b>在新分支上做</b>
            她在一个临时的 git 分支里改，你的代码一个字都不动；你说合并时先跑测试，全过才写回来（要是 git 仓库）
          </span>
        </label>
        <div class="modes">
          <label v-for="(label, m) in MODE_LABELS" :key="m" :class="{ on: newMode === m }">
            <input v-model="newMode" type="radio" :value="m" />
            <b>{{ label }}</b><span>{{ MODE_HINTS[m] }}</span>
          </label>
        </div>
        <div class="new-budget">
          <label for="new-task-budget">花费上限（元，可以不填）</label>
          <input
            id="new-task-budget"
            v-model="newBudget"
            @keydown.stop
            inputmode="decimal"
            placeholder="不限"
            :aria-invalid="Boolean(newBudgetError)"
            aria-describedby="new-task-budget-help new-task-budget-error"
          />
          <p id="new-task-budget-help" class="dim">未知价格的用量无法完整计价。达到上限后在下一步前停下，单步可能超出上限。</p>
          <p v-if="newBudgetError" id="new-task-budget-error" class="budget-error" role="alert">{{ newBudgetError }}</p>
        </div>
        <p class="dim">她只在这个文件夹里读写；删除、推送这类危险命令不管哪种模式都会先问你。</p>
        <button v-if="current" class="ghost" @click="picking = false">取消</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.app {
  --bg: #0f1117;
  --side: #12151c;
  --card: #161a23;
  --code: #0b0e14;
  --line: #262b37;
  --text: #d7dce5;
  --dim: #8a92a3;
  --accent: #a78bfa;
  --accent-bg: rgba(167, 139, 250, 0.16);
  --ok: #4ade80;
  --warn: #facc15;
  --bad: #f87171;
  display: grid;
  grid-template-columns: 240px minmax(0, 1fr) 260px;
  height: 100%;
  color: var(--text);
}

.new-budget {
  margin: 12px 0;
}

.task-budget {
  padding: 8px 18px;
  border-bottom: 1px solid var(--line);
}

.new-budget label {
  display: block;
  margin-bottom: 6px;
}

.new-budget input {
  box-sizing: border-box;
  width: min(200px, 100%);
  padding: 7px 10px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: var(--card);
  color: var(--text);
  font: inherit;
}

.new-budget input:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}

.budget-error {
  color: var(--bad);
  margin: 6px 0;
}

/* 还没打开会话：没有右边那一栏 */
.app.bare {
  grid-template-columns: 240px minmax(0, 1fr);
}

/* 窗口窄的时候先收起右边那一栏 */
@media (max-width: 1000px) {
  .app {
    grid-template-columns: 200px minmax(0, 1fr);
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

kbd {
  margin-left: 4px;
  padding: 0 4px;
  border: 1px solid var(--line);
  border-radius: 3px;
  font-size: 11px;
  color: var(--dim);
}

.dim {
  color: var(--dim);
}

/* ---------- 左边：会话 ---------- */

.side {
  display: flex;
  flex-direction: column;
  min-height: 0;
  background: var(--side);
  border-right: 1px solid var(--line);
}

.brand {
  padding: 14px 16px 10px;
  font-size: 16px;
  font-weight: 700;
  letter-spacing: 1px;
}

.brand span {
  color: var(--accent);
}

.new {
  margin: 0 12px 10px;
  padding: 8px;
  border: 1px solid var(--accent);
  border-radius: 8px;
  background: var(--accent-bg);
}

.list {
  flex: 1;
  overflow-y: auto;
}

.sess {
  padding: 8px 16px;
  cursor: pointer;
  border-left: 3px solid transparent;
}

.sess:hover {
  background: rgba(255, 255, 255, 0.03);
}

.sess.on {
  background: rgba(167, 139, 250, 0.08);
  border-left-color: var(--accent);
}

.sess .title {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.sess .meta {
  margin-top: 2px;
  font-size: 12px;
  color: var(--dim);
}

.live {
  display: inline-block;
  width: 7px;
  height: 7px;
  margin-right: 6px;
  border-radius: 50%;
  background: var(--accent);
  animation: pulse 1.2s infinite;
}

@keyframes pulse {
  50% {
    opacity: 0.3;
  }
}

.none {
  padding: 12px 16px;
  color: var(--dim);
}

.side-foot {
  padding: 10px 16px;
  border-top: 1px solid var(--line);
  font-size: 12px;
  color: var(--dim);
}

code {
  font-family: Consolas, monospace;
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
  gap: 12px;
  padding: 10px 18px;
  border-bottom: 1px solid var(--line);
}

.proj {
  flex: 1;
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.proj .path {
  margin-left: 10px;
  color: var(--dim);
  font-size: 12px;
}

.mode {
  padding: 4px 8px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: var(--card);
  color: var(--text);
}

.model {
  color: var(--dim);
  font-size: 12px;
  cursor: pointer;
}

.model:hover {
  color: var(--accent);
}

.tabs {
  display: flex;
  border: 1px solid var(--line);
  border-radius: 6px;
  overflow: hidden;
}

.tabs button {
  padding: 3px 12px;
  border: none;
  background: transparent;
  color: var(--dim);
}

.tabs button.on {
  background: var(--accent-bg);
  color: var(--text);
}

.scroll {
  flex: 1;
  overflow-y: auto;
}

.transcript {
  max-width: 920px;
  margin: 0 auto;
  padding: 18px 24px 30px;
}

.user {
  margin: 18px 0 8px;
  padding: 10px 14px;
  border-radius: 10px;
  background: rgba(167, 139, 250, 0.12);
  border: 1px solid rgba(167, 139, 250, 0.3);
  white-space: pre-wrap;
}

.md {
  margin: 6px 0;
  line-height: 1.7;
}

.md :deep(p) {
  margin: 6px 0;
}

.md :deep(h4) {
  margin: 12px 0 4px;
  font-size: 15px;
}

.md :deep(ul),
.md :deep(ol) {
  margin: 4px 0;
  padding-left: 22px;
}

.md :deep(code) {
  padding: 1px 5px;
  border-radius: 4px;
  background: var(--code);
  font-family: Consolas, monospace;
  font-size: 12.5px;
  color: #f0abfc;
}

.md :deep(pre) {
  margin: 8px 0;
  padding: 10px 12px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--code);
  overflow-x: auto;
}

.md :deep(pre code) {
  padding: 0;
  background: none;
  color: #e2e8f0;
}

.md :deep(blockquote) {
  margin: 6px 0;
  padding-left: 10px;
  border-left: 3px solid var(--line);
  color: var(--dim);
}

.md :deep(.md-link) {
  color: #7dd3fc;
}

.end {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
  margin: 10px 0 4px;
  color: var(--ok);
}

.undo-button {
  margin-left: auto;
  font-size: 12.5px;
}

.undo-dialog {
  box-sizing: border-box;
  width: min(540px, 92vw);
  max-height: 86vh;
  overflow: auto;
  padding: 18px 22px;
  border: 1px solid var(--line);
  border-radius: 12px;
  background: var(--side);
  color: var(--text);
  overflow-wrap: anywhere;
}

.undo-dialog::backdrop {
  background: rgba(0, 0, 0, 0.55);
}

.undo-dialog h2 {
  margin: 0 0 12px;
  font-size: 17px;
}

.undo-actions {
  display: flex;
  justify-content: flex-end;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 12px;
}

.undo-dialog :focus-visible,
.undo-button:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}

.undo-actions .undo-confirm {
  border-color: var(--warn);
  color: var(--warn);
}

.end.error {
  color: var(--bad);
}

.end:not(.done):not(.error) {
  color: var(--warn);
}

.end .stats {
  color: var(--dim);
  font-size: 12.5px;
}

.note {
  margin: 8px 0;
  color: var(--dim);
  font-size: 12.5px;
  text-align: center;
}

.working {
  display: flex;
  align-items: center;
  gap: 4px;
  margin: 10px 0;
  color: var(--dim);
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
  margin: 40px 0;
  text-align: center;
}

.hello.big {
  margin-top: 22vh;
}

.hello .new {
  margin-top: 12px;
  padding: 8px 18px;
}

.error {
  margin: 0 18px 6px;
  padding: 6px 12px;
  border: 1px solid rgba(248, 113, 113, 0.5);
  border-radius: 6px;
  background: rgba(248, 113, 113, 0.1);
  color: #fecaca;
  cursor: pointer;
}

.composer {
  display: flex;
  gap: 10px;
  padding: 10px 18px 14px;
  border-top: 1px solid var(--line);
}

.composer textarea {
  flex: 1;
  resize: none;
  padding: 10px 12px;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: var(--card);
  color: var(--text);
  font: inherit;
  outline: none;
}

.composer textarea:focus {
  border-color: var(--accent);
}

.send,
.stop {
  align-self: flex-end;
  padding: 8px 18px;
  border: 1px solid var(--accent);
  border-radius: 8px;
  background: var(--accent-bg);
}

.stop {
  border-color: var(--warn);
  background: rgba(250, 204, 21, 0.1);
}

/* ---------- 右边 ---------- */

.info {
  overflow-y: auto;
  padding: 6px 16px;
  background: var(--side);
  border-left: 1px solid var(--line);
}

.info section {
  padding: 10px 0;
  border-bottom: 1px solid var(--line);
}

.info h3 {
  margin: 0 0 6px;
  font-size: 13px;
  color: var(--dim);
  font-weight: 600;
}

.info ul {
  margin: 0;
  padding: 0;
  list-style: none;
}

.info li {
  padding: 2px 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.todos .completed {
  color: var(--dim);
  text-decoration: line-through;
}

.todos .in_progress {
  color: var(--accent);
}

.files li {
  font-family: Consolas, monospace;
  font-size: 12.5px;
}

.rate b {
  font-size: 20px;
  color: var(--ok);
}

.bar {
  height: 5px;
  margin: 6px 0;
  border-radius: 3px;
  background: var(--line);
  overflow: hidden;
}

.bar i {
  display: block;
  height: 100%;
  background: var(--ok);
}

.ghost {
  margin-top: 6px;
  padding: 4px 10px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: transparent;
}

/* ---------- 在新分支上做 ---------- */

.branch-badge {
  padding: 2px 8px;
  border: 1px solid rgba(125, 211, 252, 0.5);
  border-radius: 10px;
  color: #7dd3fc;
  font-size: 12px;
  font-family: Consolas, monospace;
  white-space: nowrap;
}

.branch-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 6px;
}

.merge-btn {
  padding: 5px 12px;
  border: 1px solid var(--ok);
  border-radius: 6px;
  background: rgba(74, 222, 128, 0.12);
}

.merge {
  margin: 10px 0;
  padding: 10px 12px;
  border: 1px solid rgba(250, 204, 21, 0.5);
  border-radius: 8px;
  background: rgba(250, 204, 21, 0.05);
}

.merge.ok {
  border-color: rgba(74, 222, 128, 0.55);
  background: rgba(74, 222, 128, 0.06);
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
  border-radius: 6px;
  background: var(--code);
  color: #cbd5e1;
  font-family: Consolas, monospace;
  font-size: 12px;
  white-space: pre-wrap;
}

.merge-actions {
  margin-top: 6px;
}

.isolate {
  display: flex;
  gap: 10px;
  align-items: flex-start;
  margin-top: 12px;
  padding: 8px 10px;
  border: 1px solid var(--line);
  border-radius: 8px;
  cursor: pointer;
}

.isolate.on {
  border-color: #7dd3fc;
  background: rgba(125, 211, 252, 0.08);
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

/* ---------- 选项目 ---------- */

.overlay {
  position: fixed;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.55);
  z-index: 10;
}

.picker {
  width: min(640px, 92vw);
  max-height: 86vh;
  overflow-y: auto;
  padding: 18px 22px;
  border: 1px solid var(--line);
  border-radius: 12px;
  background: var(--side);
}

.picker h2 {
  margin: 0 0 12px;
  font-size: 17px;
}

.choices {
  display: grid;
  gap: 6px;
}

.choice {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  padding: 8px 12px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--card);
  text-align: left;
}

.choice:hover {
  border-color: var(--accent);
}

.choice span {
  margin-top: 2px;
  font-size: 12px;
  color: var(--dim);
  word-break: break-all;
}

.choice.other {
  align-items: center;
  color: var(--accent);
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
  padding: 8px 10px;
  border: 1px solid var(--line);
  border-radius: 8px;
  cursor: pointer;
}

.modes label.on {
  border-color: var(--accent);
  background: var(--accent-bg);
}

.modes input {
  display: none;
}

.modes span {
  margin-top: 2px;
  font-size: 12px;
  color: var(--dim);
}
</style>
