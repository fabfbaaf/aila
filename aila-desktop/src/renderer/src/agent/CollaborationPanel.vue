<script setup lang="ts">
/** 主任务里的分工和交付记录，直接使用会话日志与已安装的模型列表。 */
import { computed, reactive, ref } from 'vue'
import type { AgentEngine, AgentEvent, AgentSessionSummary, ModelGroup, PairAction, PairPick } from '../../../shared/agent'
import { t } from '../i18n'
import { statusLine } from './board'
import ModelPicker from './ModelPicker.vue'

interface Assignment {
  id?: string
  agent: AgentEngine
  model?: string
  title: string
  task: string
  paths: string[]
}

const props = defineProps<{
  entry: AgentEvent
  events: AgentEvent[]
  sessions: AgentSessionSummary[]
  groups: ModelGroup[]
  busy: boolean
  pending: boolean
}>()
const emit = defineEmits<{
  action: [action: PairAction]
  open: [sid: string]
  models: [refresh: boolean]
  login: [engine: AgentEngine]
  install: [engine: AgentEngine]
}>()
const proposed = computed(() => props.entry.type === 'pair_proposal')
const assignments = computed(() => (props.entry.assignments ?? props.entry.members) as Assignment[])
const providers = computed(() => props.groups.filter((group) => group.engine !== 'aila'))
const picks = reactive<Record<number, PairPick>>({})
const pickAt = (member: Assignment, index: number): PairPick => picks[index] ?? { agent: member.agent, model: member.model ?? '' }
const unavailable = (member: Assignment, index: number): boolean => {
  if (!providers.value.length) return false
  const pick = pickAt(member, index)
  const group = providers.value.find((provider) => provider.engine === pick.agent)
  return !group?.ready || Boolean(pick.model && !group.models.some((model) => model.value === pick.model))
}
const canDispatch = computed(() => assignments.value.every((member, index) => !unavailable(member, index)))
const readMember = (member: Assignment): AgentSessionSummary | undefined => props.sessions.find((s) => s.id === member.id)
const pairEvents = computed(() => [...props.events].reverse().filter((e) => e.pair_id === props.entry.pair_id))
const review = computed(() => pairEvents.value.find((e) => e.type === 'pair_review'))
const outcome = computed(() => pairEvents.value.find((e) => ['pair_applied', 'pair_closed', 'pair_result'].includes(e.type)))
const tests = computed(() => (review.value?.tests ?? []) as { label: string; ok: boolean }[])
const isDone = computed(() => outcome.value?.type === 'pair_applied' || outcome.value?.type === 'pair_closed')
const canValidate = computed(() => assignments.value.every((member) => {
  const session = readMember(member)
  return !session || (session.outcome === 'done' && !session.running && !session.waiting)
}))
const expanded = ref(false)

function decide(approved: boolean): void {
  const proposal_id = String(props.entry.proposal_id)
  if (approved) emit('action', { action: 'approve', proposal_id, picks: assignments.value.map(pickAt) })
  else emit('action', { action: 'reject', proposal_id })
}
</script>

<template>
  <section class="collaboration" :aria-label="t('协作')" :aria-busy="busy || pending">
    <h3>{{ proposed ? t('确认分工') : t('协作') }}</h3>
    <details :open="proposed">
      <summary>{{ t('接口约定和验收要求') }}</summary>
      <p class="body">{{ entry.contract }}</p>
      <p class="body">{{ entry.acceptance }}</p>
      <code>{{ entry.test_file }}</code>
    </details>
    <ul>
      <li v-for="(member, index) in assignments" :key="member.id ?? index">
        <strong>{{ member.title }}</strong>
        <ModelPicker
          v-if="proposed && !entry.preset" :groups="providers" down
          :value="{ engine: pickAt(member, index).agent, value: pickAt(member, index).model }"
          :disabled="busy"
          @open="emit('models', false)" @refresh="emit('models', true)"
          @login="(engine) => emit('login', engine)" @install="(engine) => emit('install', engine)"
          @pick="(p) => { if (p.engine !== 'aila') picks[index] = { agent: p.engine, model: p.value } }"
        />
        <span v-else>{{ readMember(member)?.model ?? providers.find((group) => group.engine === member.agent)?.title ?? member.agent }}
          <template v-if="member.model"> · {{ providers.find((group) => group.engine === member.agent)?.models.find((model) => model.value === member.model)?.name ?? member.model }}</template>
        </span>
        <span v-if="proposed && !entry.preset" class="dim">{{ providers.find((g) => g.engine === pickAt(member, index).agent)?.title ?? pickAt(member, index).agent }}</span>
        <p v-if="proposed && unavailable(member, index)" class="body dim">{{ entry.preset ? t('协作者目前不可用，恢复可用后再派工。') : t('所选模型暂不可用，重新选择后再派工。') }}</p>
        <p class="body"><code>{{ member.paths.join(' · ') }}</code></p>
        <p v-if="proposed" class="body">{{ member.task }}</p>
        <template v-else>
          <span class="dim">{{ readMember(member) ? statusLine(readMember(member)!) || t('还没开始') : t('查看子任务获取状态') }}</span>
          <button v-if="member.id" class="ghost" type="button" @click="emit('open', member.id)">{{ t('打开子任务') }}</button>
        </template>
      </li>
    </ul>
    <div v-if="proposed" class="actions">
      <button class="ghost" type="button" :disabled="busy" @click="decide(false)">{{ t('退回分工') }}</button>
      <button class="ghost" type="button" :disabled="busy || !canDispatch" @click="decide(true)">{{ t('确认并派工') }}</button>
    </div>
    <template v-else>
      <p v-if="pending" role="status">{{ t('正在验收或等待合并确认') }}</p>
      <p v-else-if="outcome" class="body" role="status">{{ outcome.text }}</p>
      <div v-if="!isDone" class="actions">
        <button class="ghost" type="button" :disabled="pending || busy" @click="emit('action', { action: 'close' })">
          {{ t('结束协作，保留子任务') }}
        </button>
        <button class="ghost" type="button" :disabled="pending || busy || !canValidate" @click="emit('action', { action: 'validate' })">
          {{ t('跑联合验收') }}
        </button>
      </div>
      <details v-if="review && (pending || outcome?.type === 'pair_applied')" @toggle="expanded = ($event.target as HTMLDetailsElement).open">
        <summary>{{ t('最终改动与测试') }}</summary>
        <p class="body"><code>{{ (review.files as string[]).join(' · ') }}</code></p>
        <ul><li v-for="(test, index) in tests" :key="index">{{ test.ok ? t('通过') : t('失败') }} · {{ test.label }}</li></ul>
        <pre v-if="expanded">{{ review.detail }}</pre>
      </details>
    </template>
  </section>
</template>

<style scoped>
.collaboration { padding: 12px; border: 1px solid var(--line); border-radius: var(--ui-r); background: var(--raised); }
h3 { margin: 0 0 8px; font-size: 14px; }
ul { list-style: none; padding: 0; margin: 8px 0; }
li { border-top: 1px solid var(--line); padding: 8px 0; }
li > :is(span, button, .model-picker) { margin-inline-start: 8px; }
details { margin-block: 8px; }
summary { cursor: pointer; }
.body { white-space: pre-wrap; overflow-wrap: anywhere; margin-block: 4px; }
.actions { display: flex; flex-wrap: wrap; gap: 8px; justify-content: flex-end; }
pre { overflow: auto; max-height: 300px; padding: 8px; background: var(--ui-sunken); }
</style>
