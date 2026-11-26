<script setup lang="ts">
import { ref, watch } from 'vue'
import type { CodeSource, DecisionSource, EvidenceSource } from '../../../shared/evidence'
import { dateText, t } from '../i18n'

const props = defineProps<{ sources: EvidenceSource[] }>()
const root = ref<HTMLElement | null>(null)
const active = ref('')
const cited = ref('')

function reveal(id: string, quote = ''): void {
  const match = [...(root.value?.querySelectorAll('details') ?? [])].find((el) => el.dataset.source === id)
  if (!match) return
  active.value = id
  cited.value = quote
  match.open = true
  match.querySelector('summary')?.focus()
}
defineExpose({ reveal })
watch(() => props.sources, () => { active.value = ''; cited.value = '' })

function location(source: EvidenceSource): string {
  if ('file' in source) return `${source.file}:${source.start}–${source.end}`
  if ('decision_id' in source) return source.until ? t('历史项目约定') : t('当前项目约定')
  return [source.document, source.page ? t('第 {n} 页', { n: source.page }) : '', source.heading].filter(Boolean).join(' · ')
}

function related(source: CodeSource): string {
  if (!source.from_id) return ''
  switch (source.relation) {
    case 'symbol': return t('资料 {id} 提到的符号', { id: source.from_id })
    case 'caller': return t('可能调用 {id}', { id: source.from_id })
    case 'callee': return t('可能由 {id} 调用', { id: source.from_id })
    case 'http': return t('与 {id} 的 HTTP 关系', { id: source.from_id })
    case 'reference': return t('引用 {id} 里的符号', { id: source.from_id })
    case 'test_reference': return t('测试中引用 {id} 里的符号', { id: source.from_id })
    case 'test': return t('覆盖 {id} 的相关测试', { id: source.from_id })
    case 'ipc': return t('与 {id} 的 IPC 关系', { id: source.from_id })
    default: return ''
  }
}
function newerVersion(source: DecisionSource): DecisionSource | undefined {
  return props.sources.find((item): item is DecisionSource =>
    'decision_id' in item && item.supersedes === source.decision_id)
}

function showRevision(source: DecisionSource): void {
  const next = newerVersion(source)
  if (next) reveal(next.id)
}

function openSession(id: string): void {
  void import('../platform/bridge').then(({ bridge }) => bridge?.openAgent(id))
}

function matchLabel(method: string): string {
  switch (method) {
    case 'exact': return t('符号精确匹配')
    case 'keyword': return t('关键词匹配')
    case 'vector': return t('向量相似')
    case 'drill': return t('文件内细查')
    default: return method
  }
}

function excerpt(source: EvidenceSource): [string, string, string] {
  const start = source.id === active.value && cited.value ? source.text.indexOf(cited.value) : -1
  return start < 0 ? [source.text, '', ''] :
    [source.text.slice(0, start), cited.value, source.text.slice(start + cited.value.length)]
}
</script>

<template>
  <div ref="root" class="evidence-sources">
    <details v-for="source in sources" :key="source.id" :data-source="source.id" :class="{ active: active === source.id }">
      <summary><strong>{{ source.id }}</strong> {{ location(source) }}</summary>
      <div v-if="'file' in source" class="metadata">
        {{ [source.symbol, related(source), source.via].filter(Boolean).join(' · ') }}
      </div>
      <div v-else-if="'decision_id' in source" class="metadata">
        {{ t('生效于 {date}', { date: dateText(new Date(source.effective * 1000), true) }) }}
        <template v-if="source.until"> · {{ t('更新于 {date}', { date: dateText(new Date(source.until * 1000), true) }) }}</template>
      </div>
      <div v-else class="metadata">{{ t('资料版本 {version}', { version: source.version }) }}</div>
      <div v-if="source.matches?.length" class="metadata">{{ source.matches.map(matchLabel).join(' · ') }}</div>
      <pre>{{ excerpt(source)[0] }}<mark v-if="excerpt(source)[1]">{{ excerpt(source)[1] }}</mark>{{ excerpt(source)[2] }}</pre>
      <template v-if="'decision_id' in source">
        <button v-if="newerVersion(source)" type="button" class="link" @click="showRevision(source)">
          {{ t('查看更新后的约定') }}
        </button>
        <div v-if="source.message">
          <strong>{{ t('当时的用户消息') }}</strong>
          <pre>{{ source.message }}</pre>
        </div>
        <button v-if="source.session" type="button" class="link" @click="openSession(source.session)">
          {{ t('打开来源会话') }}
        </button>
      </template>
    </details>
  </div>
</template>

<style scoped>
.evidence-sources {
  min-width: 0;
  padding: 4px 0;
}
details {
  border-top: 1px solid var(--ui-line);
  padding: 6px 0;
}
details.active summary strong {
  color: var(--ui-accent);
}
summary {
  cursor: pointer;
  overflow-wrap: anywhere;
}
summary strong {
  font-family: var(--ui-mono);
  margin-inline-end: 4px;
}
.metadata {
  margin: 5px 0;
  color: var(--ui-muted);
  font-size: 12px;
  overflow-wrap: anywhere;
}
mark {
  color: var(--ui-text);
  background: var(--ui-accent-soft);
  box-shadow: 0 1px var(--ui-accent);
}
pre {
  max-height: 250px;
  overflow: auto;
  margin: 6px 0 2px;
  padding: 8px;
  border-radius: var(--ui-r-s);
  background: var(--ui-field);
  font: 12px/1.5 var(--ui-mono);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
</style>
