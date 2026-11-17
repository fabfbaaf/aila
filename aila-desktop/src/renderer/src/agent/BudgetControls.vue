<script setup lang="ts">
/** 任务顶部的页内预算表单，按会话保存，切换后不把旧请求结果写到新会话。 */
import { computed, nextTick, reactive, ref, watch } from 'vue'
import type { AgentBudgetEvent } from '../../../shared/agent'
import { parseBudget, yuan } from '../../../shared/budget'

const props = defineProps<{
  sid: string
  cost?: number | null
  budget: number | null
  change: (sid: string, amount: number | null) => Promise<AgentBudgetEvent>
}>()
const emit = defineEmits<{ changed: [sid: string, event: AgentBudgetEvent] }>()
const editing = ref(false)
const text = ref('')
const error = ref('')
const input = ref<HTMLInputElement>()
const pending = reactive(new Set<string>())
let generation = 0
const saving = computed(() => pending.has(props.sid))
const known = computed(() => typeof props.cost === 'number' && Number.isFinite(props.cost) && props.cost >= 0)
const label = computed(() => {
  const limit = props.budget === null ? '不限' : `上限 ${yuan(props.budget)}`
  return known.value ? `已花 ${yuan(props.cost!)} / ${limit}` : `价格未知 / ${limit}`
})

function cancel(): void {
  ++generation
  editing.value = false
  error.value = ''
}

watch(() => props.sid, cancel)

async function edit(): Promise<void> {
  if (saving.value) return
  ++generation
  editing.value = true
  error.value = ''
  text.value = props.budget === null ? '' : String(props.budget)
  await nextTick()
  input.value?.focus()
  input.value?.select()
}

async function save(): Promise<void> {
  const sid = props.sid
  if (pending.has(sid)) return
  let amount: number | null
  try { amount = parseBudget(text.value) }
  catch (err) { error.value = (err as Error).message; input.value?.focus(); return }
  error.value = ''
  pending.add(sid)
  const request = generation
  try {
    const event = await props.change(sid, amount)
    emit('changed', sid, event)
    if (props.sid === sid && generation === request) editing.value = false
  } catch (err) {
    if (props.sid === sid && generation === request) error.value = (err as Error).message || '上限没能保存，再试一次。'
  } finally { pending.delete(sid) }
}
</script>

<template>
  <div class="budget-controls">
    <button type="button" class="budget-button" :title="label" :aria-expanded="editing" aria-controls="task-budget-editor" :disabled="saving" @click="edit">
      {{ saving ? '正在保存上限…' : label }}
    </button>
    <form v-if="editing" id="task-budget-editor" class="budget-editor" @submit.prevent="save" @keydown.stop @keydown.esc.prevent.stop="cancel">
      <label for="task-budget-amount">花费上限（元，可以留空）</label>
      <div class="budget-row">
        <input
          id="task-budget-amount"
          ref="input"
          v-model="text"
          inputmode="decimal"
          placeholder="不限"
          :disabled="saving"
          :aria-invalid="Boolean(error)"
          aria-describedby="task-budget-help task-budget-error"
        />
        <button type="submit" :disabled="saving">{{ saving ? '正在保存…' : '保存' }}</button>
        <button type="button" @click="cancel">取消</button>
      </div>
      <p id="task-budget-help">
        留空表示不限，0 会在下一步前停下。达到上限后在下一步前停下，当前一步的费用可能超出上限。
      </p>
      <p v-if="!known" class="unknown">当前有价格未知的用量，无法完整计价和按此上限保护。</p>
      <p v-if="error" id="task-budget-error" class="error" role="alert">{{ error }}</p>
    </form>
  </div>
</template>

<style scoped>
/* 在任务页顶栏里：平时是一颗小胶囊「已花 / 上限」，点开在下面浮出编辑框 */
.budget-controls { position: relative; min-width: 0; }
.budget-button {
  max-width: 240px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  border-radius: 999px;
  padding: 4px 11px;
  font-family: var(--ui-mono, Consolas, monospace);
  font-size: 12px;
  color: var(--text-2, var(--text));
}
button, input {
  box-sizing: border-box;
  border: 1px solid var(--line);
  border-radius: var(--ui-r-s, 6px);
  padding: 6px 10px;
  background: var(--card);
  color: var(--text);
  font: inherit;
  transition: border-color 120ms ease, background 120ms ease;
}
button { cursor: pointer; font-size: 12.5px; }
button:disabled, input:disabled { opacity: 0.5; cursor: default; }
button:hover:enabled { border-color: var(--accent); }
button:focus-visible, input:focus-visible { outline: none; border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-bg); }
.budget-editor {
  position: absolute;
  top: calc(100% + 8px);
  right: 0;
  z-index: 30;
  width: 320px;
  padding: 12px 14px;
  border: 1px solid var(--line-strong, var(--line));
  border-radius: var(--ui-r-l, 12px);
  background: var(--raised, var(--side));
  box-shadow: var(--ui-shadow, 0 10px 30px rgba(0, 0, 0, 0.4));
}
.budget-editor label { display: block; font-size: 13px; font-weight: 600; margin-bottom: 8px; }
.budget-row { display: flex; flex-wrap: wrap; gap: 6px; }
.budget-row input { flex: 1; width: 120px; min-width: 0; font-family: var(--ui-mono, Consolas, monospace); }
.budget-row button[type='submit'] { border-color: var(--accent-line, var(--accent)); background: var(--accent-bg); }
p { margin: 8px 0 0; color: var(--dim); font-size: 12px; line-height: 1.55; }
.unknown { color: var(--warn); }
.error { color: var(--bad); }
</style>
