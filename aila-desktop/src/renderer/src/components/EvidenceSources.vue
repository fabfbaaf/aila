<script setup lang="ts">
import { ref, watch } from 'vue'
import type { CodeSource, EvidenceSource } from '../../../shared/evidence'
import { t } from '../i18n'

const props = defineProps<{ sources: EvidenceSource[] }>()
const root = ref<HTMLElement | null>(null)
const active = ref('')

function reveal(id: string): void {
  const match = [...(root.value?.querySelectorAll('details') ?? [])].find((el) => el.dataset.source === id)
  if (!match) return
  active.value = id
  match.open = true
  match.querySelector('summary')?.focus()
}
defineExpose({ reveal })
watch(() => props.sources, () => { active.value = '' })

function location(source: EvidenceSource): string {
  if ('file' in source) return `${source.file}:${source.start}–${source.end}`
  return [source.document, source.page ? t('第 {n} 页', { n: source.page }) : '', source.heading].filter(Boolean).join(' · ')
}

function related(source: CodeSource): string {
  if (!source.from_id) return ''
  switch (source.relation) {
    case 'symbol': return t('资料 {id} 提到的符号', { id: source.from_id })
    case 'caller': return t('可能调用 {id}', { id: source.from_id })
    case 'callee': return t('可能由 {id} 调用', { id: source.from_id })
    case 'http': return t('与 {id} 的 HTTP 关系', { id: source.from_id })
    case 'ipc': return t('与 {id} 的 IPC 关系', { id: source.from_id })
    default: return ''
  }
}
</script>

<template>
  <div ref="root" class="evidence-sources">
    <details v-for="source in sources" :key="source.id" :data-source="source.id" :class="{ active: active === source.id }">
      <summary><strong>{{ source.id }}</strong> {{ location(source) }}</summary>
      <div v-if="'file' in source" class="metadata">
        {{ [source.symbol, related(source), source.via].filter(Boolean).join(' · ') }}
      </div>
      <div v-else class="metadata">{{ t('资料版本 {version}', { version: source.version }) }}</div>
      <pre>{{ source.text }}</pre>
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
