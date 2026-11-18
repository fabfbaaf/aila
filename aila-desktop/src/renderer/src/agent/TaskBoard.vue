<script setup lang="ts">
/**
 * 任务看板：几个任务同时做时一眼看全。四栏：等你、在做、做完了、卡住了。
 * 卡片上能直接同意 / 拒绝她等着的那个操作（说明是代码生成的，和任务页里的一样）、停下、打开。
 * 「一次开几个」：一行一个任务，每个都在新分支上做（互不干扰，做完一个合一个）。
 */
import { computed, ref } from 'vue'
import type { AgentMode, AgentSessionSummary } from '../../../shared/agent'
import { MODE_LABELS } from '../../../shared/agent'
import Icon from '../theme/Icon.vue'
import { boardColumns, COLUMNS, MAX_BATCH, parseBatch, statusLine } from './board'
import type { ProjectChoice } from './source'
import { sessionLabel } from './sessions'

const props = defineProps<{
  sessions: AgentSessionSummary[]
  choices: ProjectChoice[]
  /** 秒 */
  now: number
  error?: string
}>()

const emit = defineEmits<{
  open: [sid: string]
  stop: [sid: string]
  answer: [sid: string, id: string, approved: boolean]
  batch: [cwd: string, mode: AgentMode, tasks: string[]]
}>()

const columns = computed(() => boardColumns(props.sessions, props.now))
const empty = computed(() => COLUMNS.every((c) => !columns.value[c.id].length))

const batchOpen = ref(false)
const batchText = ref('')
const batchCwd = ref('')
const batchMode = ref<AgentMode>('ask')
const batchError = ref('')
const batchTasks = computed(() => {
  try {
    return parseBatch(batchText.value)
  } catch {
    return []
  }
})

function openBatch(): void {
  batchError.value = ''
  if (!batchCwd.value && props.choices.length) batchCwd.value = props.choices[0].path
  batchOpen.value = true
}

function submitBatch(): void {
  batchError.value = ''
  let tasks: string[]
  try {
    tasks = parseBatch(batchText.value)
  } catch (err) {
    batchError.value = (err as Error).message
    return
  }
  if (!batchCwd.value) return void (batchError.value = '先选在哪个项目里做。')
  if (!tasks.length) return void (batchError.value = '一行写一个任务。')
  emit('batch', batchCwd.value, batchMode.value, tasks)
  batchOpen.value = false
  batchText.value = ''
}
</script>

<template>
  <section class="board">
    <header class="board-head">
      <h2>看板</h2>
      <span class="hint">几个任务同时做：每个在自己的分支上，互不干扰</span>
      <button class="primary" type="button" @click="openBatch"><Icon name="plus" :size="15" :stroke="2" />一次开几个</button>
    </header>

    <form v-if="batchOpen" class="batch" @submit.prevent="submitBatch" @keydown.stop>
      <label>
        在哪个项目
        <select v-model="batchCwd">
          <option v-for="p in choices" :key="p.path" :value="p.path">{{ p.name }} — {{ p.path }}</option>
        </select>
      </label>
      <label>
        做什么（一行一个，最多 {{ MAX_BATCH }} 个）
        <textarea
          v-model="batchText"
          rows="5"
          placeholder="给购物车加优惠券&#10;修好分页从第二页开始的问题&#10;给 utils 补测试"
        ></textarea>
      </label>
      <div class="batch-row">
        <select v-model="batchMode" aria-label="权限模式">
          <option v-for="m in (['read_only', 'ask', 'auto'] as AgentMode[])" :key="m" :value="m">{{ MODE_LABELS[m] }}</option>
        </select>
        <span class="hint">每个任务都开在新分支上；你的代码不动，做完在各自的任务里合并</span>
        <button type="button" class="ghost" @click="batchOpen = false">算了</button>
        <button type="submit" class="primary" :disabled="!batchTasks.length">开 {{ batchTasks.length || '' }} 个任务</button>
      </div>
      <div v-if="batchError" class="error">{{ batchError }}</div>
    </form>

    <div v-if="error" class="error top">{{ error }}</div>
    <p v-if="empty && !batchOpen" class="empty">还没有进行中的任务。点「一次开几个」，一行写一件事，她会同时在各自的分支上做。</p>

    <div class="cols">
      <div v-for="col in COLUMNS" :key="col.id" class="col" :class="col.id">
        <div class="col-head" :title="col.hint">
          {{ col.label }}<span>{{ columns[col.id].length }}</span>
        </div>
        <article v-for="s in columns[col.id]" :key="s.id" class="card" @click="emit('open', s.id)">
          <div class="title">{{ s.title }}</div>
          <div class="meta">
            {{ sessionLabel(s) }}<template v-if="s.branch"> · <Icon name="branch" :size="12" />{{ s.branch }}</template>
          </div>
          <div class="status" :class="col.id">{{ statusLine(s) }}</div>
          <div v-if="s.plan && s.plan.decision === 'approved'" class="meta">第 {{ Math.min(s.plan.done + 1, s.plan.stages) }}/{{ s.plan.stages }} 阶段</div>
          <div class="actions" @click.stop>
            <template v-if="s.waiting === 'confirm' && s.pending && s.running">
              <button type="button" class="ok" @click="emit('answer', s.id, s.pending.id, true)">可以</button>
              <button type="button" class="ghost" @click="emit('answer', s.id, s.pending.id, false)">不行</button>
            </template>
            <button v-if="s.running" type="button" class="ghost" @click="emit('stop', s.id)">停下</button>
            <button type="button" class="ghost" @click="emit('open', s.id)">打开</button>
          </div>
        </article>
      </div>
    </div>
  </section>
</template>

<style scoped>
.board {
  display: flex;
  flex-direction: column;
  min-width: 0;
  height: 100%;
  padding: 18px 22px;
  overflow: auto;
}

.board-head {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 14px;
}

.board-head h2 {
  margin: 0;
  font-size: 18px;
}

.hint {
  flex: 1;
  color: var(--ui-muted);
  font-size: 12px;
}

button {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 12px;
  border: 1px solid var(--ui-line-strong);
  border-radius: var(--ui-r-s);
  background: var(--ui-raised);
  color: var(--ui-text);
  font: inherit;
  font-size: 13px;
  cursor: pointer;
}

button.primary {
  border-color: var(--ui-accent-line);
  background: var(--ui-accent-soft);
  color: var(--ui-accent-hi);
}

button.ok {
  border-color: var(--ui-ok-line);
  background: var(--ui-ok-soft);
  color: var(--ui-ok);
}

button.ghost {
  background: transparent;
}

button:disabled {
  opacity: 0.5;
  cursor: default;
}

.batch {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-bottom: 16px;
  padding: 14px;
  border: 1px solid var(--ui-line-strong);
  border-radius: var(--ui-r);
  background: var(--ui-surface);
}

.batch label {
  display: flex;
  flex-direction: column;
  gap: 6px;
  color: var(--ui-muted);
  font-size: 12px;
}

.batch select,
.batch textarea {
  padding: 7px 10px;
  border: 1px solid var(--ui-line);
  border-radius: var(--ui-r-s);
  background: var(--ui-field);
  color: var(--ui-text);
  font: inherit;
  font-size: 13px;
}

.batch textarea {
  resize: vertical;
}

.batch-row {
  display: flex;
  align-items: center;
  gap: 10px;
}

.error {
  color: var(--ui-bad-text);
  font-size: 13px;
}

.empty {
  color: var(--ui-muted);
}

.cols {
  display: grid;
  grid-template-columns: repeat(4, minmax(180px, 1fr));
  gap: 12px;
  align-items: start;
}

.col-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
  padding: 0 4px;
  color: var(--ui-muted);
  font-size: 13px;
  font-weight: 600;
}

.col-head span {
  padding: 0 7px;
  border-radius: 999px;
  background: var(--ui-raised);
  font-size: 11px;
}

.col.waiting .col-head {
  color: var(--ui-warn);
}

.card {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-bottom: 8px;
  padding: 10px 12px;
  border: 1px solid var(--ui-line);
  border-radius: var(--ui-r);
  background: var(--ui-surface);
  cursor: pointer;
}

.card:hover {
  border-color: var(--ui-line-strong);
}

.col.waiting .card {
  border-color: var(--ui-warn-line);
}

.title {
  overflow: hidden;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.meta {
  display: flex;
  align-items: center;
  gap: 3px;
  overflow: hidden;
  color: var(--ui-muted);
  font-size: 12px;
  white-space: nowrap;
}

.status {
  font-size: 13px;
  line-height: 1.5;
  word-break: break-all;
}

.status.running {
  color: var(--ui-cyan);
}

.status.waiting {
  color: var(--ui-warn);
}

.status.stuck {
  color: var(--ui-bad-text);
}

.actions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 4px;
}

.actions button {
  padding: 3px 10px;
  font-size: 12px;
}
</style>
