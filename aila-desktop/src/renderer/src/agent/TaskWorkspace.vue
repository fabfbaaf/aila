<script setup lang="ts">
// 任务页里的队列、原生会话导入和多仓库工作区。打开的任务继续用普通任务详情和权限确认。
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { t } from '../i18n'
import type { AgentEngine, AgentSessionSummary, ModelGroup } from '../../../shared/agent'
import type { NativeEngine, NativeImport, NativeSession, WorkGroup, WorkQueue, WorkRecommendation } from '../../../shared/taskWork'
import ModelPicker from './ModelPicker.vue'
import { AILA, engineOf, pickLabel, type ModelPick } from './models'
import type { AgentSource, ProjectChoice } from './source'

const props = defineProps<{
  source: AgentSource
  sessions: AgentSessionSummary[]
  projects: ProjectChoice[]
  current?: AgentSessionSummary | null
  models: ModelGroup[]
  modelsBusy: boolean
}>()
const emit = defineEmits<{
  open: [sid: string, draft?: string]
  close: []
  login: [engine: AgentEngine]
  install: [engine: AgentEngine]
  models: [refresh?: boolean]
}>()

const pages = { queue: '任务队列', native: '导入会话', groups: '多仓库任务' } as const
const page = ref<keyof typeof pages>('queue')
const queued = ref<WorkQueue[]>([])
const groups = ref<WorkGroup[]>([])
const error = ref('')
const busy = ref(false)
const notice = ref('')
let refreshing = false
let timer = 0

const cwd = ref(props.current?.kind === 'project' ? props.current.cwd : props.projects[0]?.path ?? '')
const title = ref('')
const prompt = ref('')
const isolate = ref(true)
const dependencies = ref<string[]>([])
const pick = ref<ModelPick>(AILA)
const useFallback = ref(false)
const fallback = ref<ModelPick>(AILA)
const verdictId = ref('')
const rework = ref<{ sid: string; note: string } | null>(null)
const reworkField = ref<HTMLTextAreaElement | null>(null)
const recommendation = ref<WorkRecommendation | null>(null)
const limit = ref(1)
const modelLoading = ref(false)
const queueStatus = {
  queued: '排队中', starting: '正在开工', running: '在做', review: '待验收', stopped: '已停下', canceled: '已取消', accepted: '已验收'
}
const dependencyStatus = { pending: '等待验收', rejected: '验收未过', canceled: '已取消', missing: '会话已删除' }
const queuesBySession = computed(() => new Set(queued.value.map((item) => item.sid)))
const prerequisites = computed(() => [
  ...queued.value.filter((item) => item.state !== 'canceled').map((item) => ({ id: item.id, title: item.title })),
  ...props.sessions.filter((item) => item.kind === 'project' && !item.handed_to && !queuesBySession.value.has(item.id))
])
const verdicts = computed(() => props.sessions.filter((item) => item.kind === 'project' && !item.running && item.outcome && !item.handed_to))
const readyToPick = (model: ModelPick) => model.engine === 'aila' || props.models.some(
  (group) => group.engine === model.engine && group.ready && group.logged_in !== false)
const queueReady = computed(() => readyToPick(pick.value) && (!useFallback.value || readyToPick(fallback.value)))
const readyRecommendations = computed(() => (recommendation.value?.models ?? []).filter((row) =>
  props.models.some(group => group.engine === row.engine && group.ready &&
    group.logged_in !== false && group.models.some(model => row.engine === 'aila' ? model.name === row.model : model.value === row.model))))

const engine = ref<NativeEngine>('codex')
const nativeCwd = ref(cwd.value)
const nativeId = ref('')
const nativeTitle = ref('')
const nativeSessions = ref<NativeSession[]>([])
const nativeCursor = ref<string | null>(null)
const manual = ref(false)
const imported = ref<NativeImport | null>(null)

const groupTitle = ref('')
const repos = ref<string[]>([])
const contract = ref('')
const acceptance = ref('')
const groupPick = ref<ModelPick>(AILA)
const workModel = (model: ModelPick) => ({ engine: engineOf(model), model: model.value })
const label = (model: ModelPick) => pickLabel(props.models, model)

async function refresh(): Promise<void> {
  if (refreshing) return
  refreshing = true
  try {
    const state = await props.source.taskWork({ action: 'state' })
    queued.value = state.queue
    groups.value = state.groups
  } catch (err) {
    if (!error.value) error.value = (err as Error).message
  } finally {
    refreshing = false
  }
}

async function perform(action: () => Promise<void>): Promise<void> {
  if (busy.value) return
  busy.value = true
  error.value = notice.value = ''
  try {
    await action()
    await refresh()
  } catch (err) {
    error.value = (err as Error).message
  } finally {
    busy.value = false
  }
}

function switchPage(next: keyof typeof pages): void {
  page.value = next
  error.value = notice.value = ''
}

let modelRequest = 0
async function loadRecommendations(): Promise<void> {
  const request = ++modelRequest
  recommendation.value = null
  limit.value = 1
  modelLoading.value = !!cwd.value
  if (!cwd.value) return
  try {
    const response = await props.source.taskWork({ action: 'models', cwd: cwd.value })
    if (request !== modelRequest) return
    recommendation.value = response
    limit.value = response.limit
  } catch (err) {
    if (request === modelRequest && !error.value) error.value = (err as Error).message
  } finally {
    if (request === modelRequest) modelLoading.value = false
  }
}

async function enqueue(): Promise<void> {
  await perform(async () => {
    await props.source.taskWork({
      action: 'queue', cwd: cwd.value, title: title.value, prompt: prompt.value, pick: workModel(pick.value),
      fallback: useFallback.value ? workModel(fallback.value) : null, dependencies: dependencies.value, isolate: isolate.value
    })
    title.value = prompt.value = ''
    dependencies.value = []
    notice.value = t('已加入任务队列。')
  })
}

function decide(sid: string, accepted: boolean, note = ''): void {
  void perform(async () => {
    await props.source.taskWork({ action: 'verdict', sid, accepted, note })
    if (rework.value?.sid === sid) rework.value = null
    notice.value = accepted ? t('已记录验收通过。') : t('已退回修改，任务会接着做。')
    await loadRecommendations()
  })
}

function requestRework(sid: string): void {
  rework.value = { sid, note: '' }
  void nextTick(() => reworkField.value?.focus())
}

function submitRework(): void {
  if (rework.value?.note.trim()) decide(rework.value.sid, false, rework.value.note)
}

function scan(more = false): void {
  if (!more) {
    nativeSessions.value = []
    nativeCursor.value = null
    manual.value = false
  }
  void perform(async () => {
    const result = await props.source.taskWork({
      action: 'native-list', engine: engine.value, cwd: nativeCwd.value, cursor: nativeCursor.value ?? undefined
    })
    nativeSessions.value = [...nativeSessions.value, ...result.sessions.filter((s) => !nativeSessions.value.some((n) => n.id === s.id))]
    nativeCursor.value = result.next_cursor
    manual.value = result.manual
  })
}

function selectNative(session: NativeSession): void {
  nativeCwd.value = session.cwd
  nativeId.value = session.id
  nativeTitle.value = session.title.slice(0, 100)
  imported.value = null
}

function adopt(): void {
  void perform(async () => {
    imported.value = await props.source.taskWork({
      action: 'native-import', engine: engine.value, cwd: nativeCwd.value, native_id: nativeId.value, title: nativeTitle.value
    })
  })
}

function groupSession(group: WorkGroup, member?: string, pr = false): void {
  void perform(async () => {
    const result = await props.source.taskWork({ action: 'group-session', group: group.id, member })
    const draft = pr ? '/pr' : member || group.integration ? undefined : t('按联调验收条件检查这组分支，记录实际运行的命令和结果。')
    emit('open', result.sid, draft)
  })
}

async function addRepo(): Promise<void> {
  const folder = await props.source.pickFolder()
  if (folder && !repos.value.includes(folder)) repos.value.push(folder)
}

function createGroup(): void {
  void perform(async () => {
    await props.source.taskWork({ action: 'groups', title: groupTitle.value, repos: repos.value,
      contract: contract.value, acceptance: acceptance.value, pick: workModel(groupPick.value) })
    groupTitle.value = contract.value = acceptance.value = ''
    repos.value = []
    notice.value = t('各仓库的任务分支已就绪。')
  })
}

watch(cwd, (_next, _prev, onCleanup) => {
  const pending = window.setTimeout(() => void loadRecommendations(), 300)
  onCleanup(() => window.clearTimeout(pending))
})
watch(engine, () => {
  nativeSessions.value = []
  nativeCursor.value = null
  nativeId.value = nativeTitle.value = ''
  imported.value = null
})
onMounted(() => {
  if (props.current) verdictId.value = props.current.id
  void refresh()
  void loadRecommendations()
  emit('models')
  timer = window.setInterval(() => void refresh(), 2000)
})
onBeforeUnmount(() => window.clearInterval(timer))
</script>

<template>
  <section class="work" :aria-label="t('任务管理')">
    <header>
      <h2>{{ t('任务管理') }}</h2>
      <button type="button" @click="emit('close')">{{ t('返回任务') }}</button>
    </header>
    <nav :aria-label="t('任务管理')">
      <button v-for="(name, key) in pages" :key="key" type="button" :aria-pressed="page === key"
        :class="{ active: page === key }" @click="switchPage(key)">{{ t(name) }}</button>
    </nav>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-if="busy || notice" role="status">{{ busy ? t('正在处理…') : notice }}</p>
    <section v-if="page === 'queue'" class="stack">
      <form class="card" @submit.prevent="enqueue">
        <h3>{{ t('排一个任务') }}</h3>
        <label>{{ t('项目目录') }}
          <input v-model.trim="cwd" list="work-projects" maxlength="1024" required>
        </label>
        <datalist id="work-projects"><option v-for="p in projects" :key="p.path" :value="p.path">{{ p.name }}</option></datalist>
        <div class="row">
          <button type="button" @click="async () => { const folder = await source.pickFolder(); if (folder) cwd = folder }">{{ t('选文件夹') }}</button>
          <label class="limit">{{ t('项目并发上限') }}<input v-model.number="limit" type="number" min="1" max="12"></label>
          <button type="button" :disabled="busy || !cwd || !Number.isInteger(limit) || limit < 1 || limit > 12"
            @click="perform(async () => { await source.taskWork({ action: 'limit', cwd, limit }); notice = t('并发上限已保存。') })">{{ t('保存上限') }}</button>
        </div>
        <details :open="!!recommendation?.models.length">
          <summary>{{ t('根据项目验收推荐模型') }}</summary>
          <p v-if="modelLoading">{{ t('正在统计验收记录…') }}</p>
          <p v-else-if="!recommendation?.models.length">{{ t('这个项目还没有可用的模型验收样本。') }}</p>
          <ol v-else class="metrics">
            <li v-for="model in recommendation.models.slice(0, 5)" :key="model.engine + model.model">
              <div><b>{{ label({ engine: model.engine, value: model.model }) }}</b>
                <span>{{ t('通过 {passed}/{samples} · 中位 {seconds} 秒 · 平均补做 {rework} 轮', model) }}</span>
              </div>
              <button v-if="readyRecommendations.includes(model)" type="button"
                @click="pick = { engine: model.engine, value: model.engine === 'aila' ? '' : model.model }">{{ t('用这个模型') }}</button>
            </li>
          </ol>
        </details>
        <label>{{ t('任务名') }}<input v-model.trim="title" maxlength="100" required></label>
        <label>{{ t('要做什么') }}<textarea v-model.trim="prompt" rows="3" maxlength="20000" required></textarea></label>
        <fieldset v-if="prerequisites.length">
          <legend>{{ t('等这些任务验收后开工') }}</legend>
          <div class="checklist">
            <label v-for="item in prerequisites" :key="item.id" class="check" :title="item.title">
              <input v-model="dependencies" type="checkbox" :value="item.id"
                :disabled="dependencies.length >= 30 && !dependencies.includes(item.id)">{{ item.title }}
            </label>
          </div>
        </fieldset>
        <fieldset>
          <legend>{{ t('主模型') }}</legend>
          <ModelPicker :groups="models" :value="pick" :loading="modelsBusy" inline @pick="pick = $event"
            @login="emit('login', $event)" @install="emit('install', $event)" @refresh="emit('models', true)" />
          <label class="check"><input v-model="useFallback" type="checkbox">{{ t('额度用完时换备用模型') }}</label>
          <ModelPicker v-if="useFallback" :groups="models" :value="fallback" :loading="modelsBusy" inline
            @pick="fallback = $event" @login="emit('login', $event)" @install="emit('install', $event)" @refresh="emit('models', true)" />
        </fieldset>
        <label class="check"><input v-model="isolate" type="checkbox">{{ t('在新分支上做') }}</label>
        <button class="primary" type="submit" :disabled="busy || !queueReady">{{ t('加入队列') }}</button>
      </form>
      <div v-if="verdicts.length" class="card">
        <h3>{{ t('记录验收') }}</h3>
        <label>{{ t('选择已停下的任务') }}<select v-model="verdictId">
          <option value="">{{ t('选一个任务') }}</option>
          <option v-for="session in verdicts" :key="session.id" :value="session.id">{{ session.title }} · {{ session.cwd }}</option>
        </select></label>
        <div class="row">
          <button type="button" :disabled="busy || !verdicts.some(s => s.id === verdictId)" @click="decide(verdictId, true)">{{ t('验收通过') }}</button>
          <button type="button" :disabled="busy || !verdicts.some(s => s.id === verdictId)" @click="requestRework(verdictId)">{{ t('需要返工') }}</button>
        </div>
      </div>
      <form v-if="rework" class="card" @submit.prevent="submitRework">
        <h3>{{ t('退回修改') }}</h3>
        <label>{{ t('总的意见') }}
          <textarea ref="reworkField" v-model.trim="rework.note" rows="3" maxlength="4000" required :disabled="busy"></textarea>
        </label>
        <div class="row">
          <button type="submit" class="primary" :disabled="busy || !rework.note">{{ t('退回修改') }}</button>
          <button type="button" :disabled="busy" @click="rework = null">{{ t('取消') }}</button>
        </div>
      </form>
      <div v-if="queued.length" class="card">
        <h3>{{ t('排队记录') }}</h3>
        <article v-for="item in queued" :key="item.id" class="entry">
          <div><b>{{ item.title }}</b><span class="badge">{{ t(queueStatus[item.state]) }}</span></div>
          <small>{{ item.project }}</small>
          <p v-for="dep in item.waiting" :key="dep.id">{{ dep.title }} · {{ t(dependencyStatus[dep.status]) }}</p>
          <p v-if="item.error" class="error">{{ item.error }}</p>
          <p v-if="item.attempts.length > 1">{{ t('已用备用模型接着做。') }}</p>
          <div class="row">
            <button v-if="item.sid" type="button" @click="emit('open', item.sid)">{{ t('打开任务') }}</button>
            <template v-if="item.sid && item.state === 'review'">
              <button type="button" :disabled="busy" @click="decide(item.sid, true)">{{ t('验收通过') }}</button>
              <button type="button" :disabled="busy" @click="requestRework(item.sid)">{{ t('需要返工') }}</button>
            </template>
            <button v-if="item.state === 'queued'" type="button" :disabled="busy"
              @click="perform(async () => { await source.taskWork({ action: 'cancel', id: item.id }) })">{{ t('取消排队') }}</button>
          </div>
        </article>
      </div>
    </section>
    <section v-else-if="page === 'native'" class="stack">
      <form class="card" @submit.prevent="adopt">
        <h3>{{ t('接手电脑上的会话') }}</h3>
        <p>{{ t('先在原应用停下这一轮，再选择原会话的账号和工作目录。') }}</p>
        <label>{{ t('原来的账号') }}<select v-model="engine" :disabled="busy">
          <option value="codex">Codex</option><option value="codex-alt">{{ t('Codex 第二账号') }}</option>
          <option value="claude">Claude Code</option><option value="claude-alt">{{ t('Claude Code 第二账号') }}</option>
        </select></label>
        <label>{{ t('原来的工作目录') }}<input v-model.trim="nativeCwd" maxlength="1024" required :disabled="busy"></label>
        <div class="row">
          <button type="button" :disabled="busy" @click="async () => { const folder = await source.pickFolder(); if (folder) nativeCwd = folder }">{{ t('选文件夹') }}</button>
          <button type="button" :disabled="busy || !nativeCwd" @click="scan()">{{ t('查找原生会话') }}</button>
        </div>
        <p v-if="manual">{{ t('这个适配器可以续跑。用原应用里的会话编号来导入。') }}</p>
        <div v-if="nativeSessions.length" class="checklist">
          <button v-for="session in nativeSessions" :key="session.id" type="button" :disabled="busy"
            :aria-pressed="session.id === nativeId" @click="selectNative(session)">
            {{ session.title }} <small v-if="session.updated">{{ session.updated }}</small>
          </button>
        </div>
        <button v-if="nativeCursor" type="button" :disabled="busy" @click="scan(true)">{{ t('继续查找') }}</button>
        <label>{{ t('原会话编号') }}<input v-model.trim="nativeId" maxlength="200" required :disabled="busy"></label>
        <button class="primary" type="submit" :disabled="busy">{{ t('加载原会话') }}</button>
      </form>
      <div v-if="imported" class="card">
        <h3>{{ t('会话已导入') }}</h3>
        <p>{{ imported.cwd }} <span v-if="imported.branch">· {{ imported.branch }}</span></p>
        <p>{{ t('适配器带回了 {messages} 条历史消息。当前工作区有 {changes} 条未提交改动。', {
          messages: imported.messages, changes: imported.changes.length
        }) }}</p>
        <ul v-if="imported.changes.length" class="checklist">
          <li v-for="line in imported.changes" :key="line"><code>{{ line }}</code></li>
        </ul>
        <button class="primary" type="button" @click="emit('open', imported.sid)">{{ t('打开任务') }}</button>
      </div>
    </section>
    <section v-else class="stack">
      <form class="card" @submit.prevent="createGroup">
        <h3>{{ t('建立多仓库工作区') }}</h3>
        <label>{{ t('任务名') }}<input v-model.trim="groupTitle" maxlength="100" required></label>
        <div v-for="(repo, index) in repos" :key="repo" class="entry row">
          <code>{{ repo }}</code><button type="button" :disabled="busy" @click="repos.splice(index, 1)">{{ t('移除') }}</button>
        </div>
        <button type="button" :disabled="busy || repos.length >= 8" @click="addRepo">{{ t('添加 Git 仓库') }}</button>
        <label>{{ t('共享接口约定') }}<textarea v-model.trim="contract" rows="2" maxlength="6000" required></textarea></label>
        <label>{{ t('联调验收条件') }}<textarea v-model.trim="acceptance" rows="2" maxlength="6000" required></textarea></label>
        <fieldset>
          <legend>{{ t('各仓库的初始模型') }}</legend>
          <ModelPicker :groups="models" :value="groupPick" :loading="modelsBusy" inline @pick="groupPick = $event"
            @login="emit('login', $event)" @install="emit('install', $event)" @refresh="emit('models', true)" />
        </fieldset>
        <button class="primary" type="submit" :disabled="busy || repos.length < 2 || !readyToPick(groupPick)">{{ t('为各仓库建立任务分支') }}</button>
      </form>
      <article v-for="group in groups" :key="group.id" class="card">
        <h3>{{ group.title }}</h3>
        <details><summary>{{ t('共享接口与验收条件') }}</summary><p class="pre">{{ group.contract }}</p><p class="pre">{{ group.acceptance }}</p></details>
        <div v-for="member in group.members" :key="member.repo" class="entry">
          <p>{{ member.repo }}</p>
          <small><code>{{ member.branch }}</code></small>
          <div v-if="member.sid" class="row">
            <button type="button" :disabled="busy" @click="groupSession(group, member.sid)">{{ t('打开任务') }}</button>
            <button type="button" :disabled="busy" @click="groupSession(group, member.sid, true)">{{ t('准备 PR') }}</button>
          </div>
          <p v-else>{{ t('会话已删除') }}</p>
        </div>
        <details v-if="group.integration_result?.commands.length" open>
          <summary>{{ t('最近一轮联调命令') }}</summary>
          <ul class="metrics">
            <li v-for="command in group.integration_result.commands" :key="command.id">
              <code>{{ command.command }}</code>
              <span>{{ command.exit_code === null ? t('命令超时') : t('退出码 {code}', { code: command.exit_code }) }}</span>
            </li>
          </ul>
        </details>
        <button type="button" :disabled="busy" @click="groupSession(group)">{{ group.integration ? t('查看联调会话') : t('打开联调任务') }}</button>
      </article>
    </section>
  </section>
</template>

<style scoped>
.work { min-width: 0; height: 100%; overflow: auto; display: block; padding: 20px clamp(14px, 3vw, 38px) 36px; color: var(--ui-text); }
header, nav, .row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
header { justify-content: space-between; }
h2, h3, p { margin: 0; }
h2 { font-size: 20px; }
h3 { font-size: 15px; }
nav, .work > p { margin: 12px 0; }
.stack { display: grid; gap: 12px; max-width: 900px; }
.card { border: 1px solid var(--ui-line); border-radius: var(--ui-r); background: var(--ui-surface); display: grid; gap: 10px; padding: 14px; justify-items: start; }
.card > *, fieldset { min-width: 0; max-width: 100%; }
.card > label, .card > details, .card > fieldset, .entry { width: 100%; }
label { display: grid; gap: 5px; }
input, textarea, select, button { color: inherit; background: var(--ui-field); border-radius: var(--ui-r-s); border: 1px solid var(--ui-line); font: inherit; padding: 7px 9px; min-width: 0; }
textarea { resize: vertical; }
button { cursor: pointer; }
button:disabled { opacity: .52; cursor: default; }
button.active, .primary { color: var(--ui-cyan); border-color: var(--ui-cyan); }
fieldset, details { border: 1px solid var(--ui-line); border-radius: var(--ui-r-s); margin: 0; padding: 8px; }
fieldset .check { margin-top: 8px; }
summary { cursor: pointer; }
.limit { display: flex; align-items: center; }
.limit input { width: 64px; }
.check { display: flex; align-items: center; gap: 6px; overflow-wrap: anywhere; }
.checklist { display: grid; gap: 4px; max-height: 190px; overflow: auto; }
.entry, .metrics li { border-top: 1px solid var(--ui-line); padding: 8px 0; }
.entry p, .entry .row, .metrics { margin-top: 6px; }
.metrics { margin-bottom: 0; padding-left: 24px; }
.metrics span { display: block; }
small, .metrics span { color: var(--ui-muted); overflow-wrap: anywhere; }
.badge { color: var(--ui-cyan); margin-left: 8px; font-size: 12px; }
.error { color: var(--ui-bad-text); overflow-wrap: anywhere; }
.entry code, .metrics code { overflow-wrap: anywhere; }
.pre { white-space: pre-wrap; margin-top: 8px; overflow-wrap: anywhere; }
</style>
