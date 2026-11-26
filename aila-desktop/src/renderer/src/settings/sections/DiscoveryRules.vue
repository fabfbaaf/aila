<script setup lang="ts">
/** 设置里的关注规则与发现历史。编辑规则时保留当时的历史依据。 */
import { nextTick, onActivated, onMounted, ref, watch } from 'vue'
import {
  DISCOVERY_DELIVERIES, DISCOVERY_FOCUSES, DISCOVERY_NAMES, isDiscoverySource,
  type Discovery, type DiscoveryDelivery, type DiscoveryFocus, type DiscoveryOrigin,
  type DiscoveryRule, type DiscoverySource, type DiscoveryTopic
} from '../../../../shared/discoveries'
import { lang, t } from '../../i18n'
import { bridge } from '../../platform/bridge'

const api = bridge!
const sources = Object.keys(DISCOVERY_NAMES).filter(isDiscoverySource)
const topics = ref<DiscoveryTopic[] | null>(null)
const panel = ref<HTMLElement | null>(null)
const draft = ref<DiscoveryRule | null>(null)
const editing = ref<DiscoverySource | null>(null)
const saving = ref(false)
const loading = ref(false)
const ruleError = ref('')
const historyOpen = ref(false)
const historyFilter = ref<DiscoverySource | ''>('')
const reports = ref<Discovery[]>([])
const nextBefore = ref<string | null>(null)
const historyBusy = ref(false)
const historyError = ref('')
let historyRequest = 0

const FOCUS_LABEL: Record<DiscoveryFocus, string> = {
  all: '所有官方更新',
  releases: '模型发布',
  pricing: '定价变化',
  web: '联网能力'
}
const DELIVERY_LABEL: Record<DiscoveryDelivery, string> = {
  daily: '有发现时提醒',
  weekly: '每周最多提醒一次',
  history: '只记进发现历史'
}
const ORIGIN_LABEL: Record<DiscoveryOrigin, string> = {
  chat: '你在对话里明确说过',
  learned: '你多次聊到了这个主题',
  manual: '你在设置里定了这条规则',
  legacy: '早期记录'
}
const STATUS_LABEL: Record<Discovery['status'], string> = {
  pending: '待提醒',
  shown: '已提醒',
  history: '仅存历史'
}

function topicOf(source: DiscoverySource): DiscoveryTopic | undefined {
  return topics.value?.find((topic) => topic.source === source)
}

function focusText(focuses: DiscoveryFocus[]): string {
  return focuses.map((focus) => t(FOCUS_LABEL[focus])).join(' · ')
}

function reportDate(unix: number): string {
  return new Date(unix * 1000).toLocaleString(lang.value === 'en' ? 'en' : 'zh-CN', {
    year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'
  })
}

function edit(source: DiscoverySource): void {
  const topic = topicOf(source)
  ruleError.value = ''
  editing.value = source
  draft.value = {
    source,
    focuses: topic?.focuses.includes('all') || !topic ? [...DISCOVERY_FOCUSES] : [...topic.focuses],
    delivery: topic?.delivery ?? 'daily',
    preference: topic?.preference ?? ''
  }
  void nextTick(() => panel.value?.querySelector<HTMLInputElement>('form input')?.focus())
}

function cancelEdit(): void {
  const source = editing.value
  editing.value = null
  draft.value = null
  if (source) void nextTick(() => panel.value?.querySelector<HTMLElement>('[data-edit="' + source + '"]')?.focus())
}

async function refresh(): Promise<void> {
  if (loading.value || saving.value) return
  loading.value = true
  ruleError.value = ''
  try {
    const result = await api.discoveryState()
    if (result.ok && result.data) topics.value = result.data.topics
    else ruleError.value = result.error ?? t('现在看不了关注清单。')
  } finally {
    loading.value = false
  }
}

async function writeRule(rule: DiscoveryRule, pause = false): Promise<void> {
  if (saving.value || loading.value) return
  saving.value = true
  ruleError.value = ''
  try {
    const result = pause ? await api.discoveryChange(rule.source, 'pause') : await api.discoverySave(rule)
    if (result.ok && result.data) {
      topics.value = result.data.topics
      cancelEdit()
      if (historyOpen.value) void loadHistory(true)
    } else ruleError.value = result.error ?? t('这次没改成，过会儿再试。')
  } finally {
    saving.value = false
  }
}

function save(): void {
  const form = draft.value
  if (form?.focuses.length) void writeRule({ ...form, focuses: [...form.focuses] })
}

async function loadHistory(reset: boolean): Promise<void> {
  if (!reset && (historyBusy.value || !nextBefore.value)) return
  const request = ++historyRequest
  historyBusy.value = true
  historyError.value = ''
  if (reset) {
    reports.value = []
    nextBefore.value = null
  }
  try {
    const result = await api.discoveryHistory(historyFilter.value || undefined, nextBefore.value || undefined)
    if (request !== historyRequest) return
    if (result.ok && result.data) {
      reports.value.push(...result.data.reports)
      nextBefore.value = result.data.next_before
    } else historyError.value = result.error ?? t('现在看不了发现历史。')
  } finally {
    if (request === historyRequest) historyBusy.value = false
  }
}

function toggleHistory(event: Event): void {
  historyOpen.value = (event.currentTarget as HTMLDetailsElement).open
  if (historyOpen.value) void loadHistory(true)
}

watch(historyFilter, () => { if (historyOpen.value) void loadHistory(true) })
onMounted(refresh)
onActivated(refresh)
</script>

<template>
  <div ref="panel" class="discovery-rules">
    <div class="section-head">
      <h4>{{ t('关注规则') }}</h4>
      <button class="btn" type="button" :disabled="loading || saving" @click="refresh">{{ t('刷新关注清单') }}</button>
    </div>
    <p v-if="loading" class="hint" role="status">{{ t('正在读取关注规则…') }}</p>
    <ul v-if="topics" class="entries">
      <li v-for="source in sources" :key="source" class="rule">
        <div class="rule-head">
          <div>
            <strong>{{ DISCOVERY_NAMES[source] }}</strong>
            <div v-if="topicOf(source)?.state === 'active'" class="hint">
              {{ focusText(topicOf(source)!.focuses) }} · {{ t(DELIVERY_LABEL[topicOf(source)!.delivery]) }}
            </div>
            <div v-else class="hint">{{ topicOf(source)?.state === 'muted' ? t('已暂停') : t('未关注') }}</div>
          </div>
          <div v-if="editing !== source" class="inline-actions">
            <button class="btn" type="button" :data-edit="source" :disabled="saving || loading || !!editing" @click="edit(source)">
              {{ topicOf(source)?.state === 'active' ? t('编辑规则') :
                topicOf(source)?.state === 'muted' ? t('恢复关注') : t('添加关注') }}
            </button>
            <button v-if="topicOf(source)?.state === 'active'" class="btn" type="button" :disabled="saving || loading || !!editing"
              @click="writeRule(topicOf(source)!, true)">{{ t('停止关注') }}</button>
          </div>
        </div>

        <form v-if="draft && editing === source" class="rule-form" :aria-label="t('编辑 {name} 的关注规则', { name: DISCOVERY_NAMES[source] })"
          @submit.prevent="save">
          <fieldset :disabled="saving">
            <legend>{{ t('关注哪些更新') }}</legend>
            <label v-for="focus in DISCOVERY_FOCUSES" :key="focus">
              <input v-model="draft.focuses" type="checkbox" :value="focus" /> {{ t(FOCUS_LABEL[focus]) }}
            </label>
          </fieldset>
          <label>
            {{ t('提醒频率') }}
            <select v-model="draft.delivery" :disabled="saving">
              <option v-for="delivery in DISCOVERY_DELIVERIES" :key="delivery" :value="delivery">{{ t(DELIVERY_LABEL[delivery]) }}</option>
            </select>
          </label>
          <label>
            {{ t('补充条件（可选）') }}
            <input v-model="draft.preference" type="text" maxlength="180" :disabled="saving" />
            <span class="hint">{{ t('例如，只在降价或新增联网能力时告诉我。') }}</span>
          </label>
          <p v-if="draft.delivery !== 'history'" class="hint">{{ t('所有来源合计每天最多提醒一次。') }}</p>
          <div class="inline-actions">
            <button class="btn primary" type="submit" :disabled="saving || loading || !draft.focuses.length">{{ t('保存关注规则') }}</button>
            <button class="btn" type="button" :disabled="saving" @click="cancelEdit">{{ t('取消') }}</button>
          </div>
          <p v-if="ruleError" class="msg error" role="alert">{{ ruleError }}</p>
        </form>
      </li>
    </ul>
    <p v-if="ruleError && !editing" class="msg error" role="alert">{{ ruleError }}</p>

    <details class="history" @toggle="toggleHistory">
      <summary>{{ t('发现历史') }}</summary>
      <div class="history-filter">
        <label>
          {{ t('来源') }}
          <select v-model="historyFilter">
            <option value="">{{ t('全部来源') }}</option>
            <option v-for="source in sources" :key="source" :value="source">{{ DISCOVERY_NAMES[source] }}</option>
          </select>
        </label>
        <button class="btn" type="button" :disabled="historyBusy" @click="loadHistory(true)">{{ t('刷新发现历史') }}</button>
      </div>
      <p v-if="historyError" class="msg error" role="alert">{{ historyError }}</p>
      <p v-if="historyBusy" class="hint" role="status">{{ t('正在读取发现历史…') }}</p>
      <p v-else-if="!reports.length && !historyError" class="hint">{{ t('还没有发现记录。') }}</p>
      <details v-for="report in reports" :key="report.id" class="report">
        <summary>
          <strong>{{ report.title }}</strong>
          <span class="hint">{{ DISCOVERY_NAMES[report.source] }} · {{ reportDate(report.created) }} · {{ t(STATUS_LABEL[report.status]) }}</span>
        </summary>
        <p>{{ report.summary }}</p>
        <div v-if="report.rule" class="why">
          <strong>{{ t('为什么记下这条') }}</strong>
          <p>{{ t(ORIGIN_LABEL[report.rule.origin]) }}<template v-if="report.rule.focuses.length"> · {{ focusText(report.rule.focuses) }}</template></p>
          <blockquote v-if="report.rule.preference">{{ report.rule.preference }}</blockquote>
        </div>
        <div v-if="report.excerpt" class="why">
          <strong>{{ t('官方原文摘录') }}</strong>
          <blockquote>{{ report.excerpt }}</blockquote>
        </div>
        <button class="btn" type="button" @click="api.discoverySource(report.source)">{{ t('看官方来源') }}</button>
      </details>
      <button v-if="nextBefore" class="btn older" type="button" :disabled="historyBusy" @click="loadHistory(false)">
        {{ t('加载更早的发现') }}
      </button>
    </details>
  </div>
</template>

<style scoped>
.discovery-rules {
  border-top: 1px solid var(--line);
  margin-top: 8px;
  padding-top: 12px;
}
.section-head,
.rule-head,
.history-filter,
.inline-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.section-head,
.rule-head {
  justify-content: space-between;
}
.section-head h4 {
  margin: 0;
}
.entries {
  list-style: none;
  margin: 4px 0 0;
  padding: 0;
}
.rule,
.report {
  padding: 12px 0;
  border-bottom: 1px solid var(--line);
  overflow-wrap: anywhere;
}
.rule-form {
  display: grid;
  gap: 12px;
  margin-top: 10px;
  padding: 12px;
  background: var(--bg);
  border-radius: var(--ui-r-s);
}
.rule-form fieldset {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 16px;
  min-width: 0;
  margin: 0;
  padding: 6px 0 0;
  border: 0;
}
.rule-form > label {
  display: grid;
  gap: 4px;
}
.rule-form p {
  margin: 0;
}
.history {
  margin-top: 12px;
}
summary {
  cursor: pointer;
  min-height: 28px;
}
.history > summary {
  font-weight: 600;
}
.history-filter {
  margin: 8px 0 0;
  align-items: end;
}
.history-filter label {
  display: grid;
  gap: 4px;
}
.why p {
  margin: 4px 0;
}
blockquote {
  margin: 4px 0 12px;
  padding: 6px 10px;
  border-inline-start: 2px solid var(--field-line);
  white-space: pre-wrap;
}
.older {
  margin-top: 10px;
}
</style>
