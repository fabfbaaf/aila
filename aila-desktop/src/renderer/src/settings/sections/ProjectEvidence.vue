<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import type { ProjectAnswer, ProjectDocuments } from '../../../../shared/evidence'
import EvidenceSources from '../../components/EvidenceSources.vue'
import { t } from '../../i18n'
import { bridge } from '../../platform/bridge'

const props = defineProps<{ projectId: string }>()
const api = bridge!
const catalog = ref<ProjectDocuments | null>(null)
const selected = ref<string[]>([])
const query = ref('')
const report = ref<ProjectAnswer | null>(null)
const evidence = ref<InstanceType<typeof EvidenceSources> | null>(null)
const busy = ref<'load' | 'save' | 'ask' | ''>('')
const error = ref('')
const dirty = computed(() => selected.value.length !== catalog.value?.linked.length
  || selected.value.some((id) => !catalog.value?.linked.includes(id)))

function update(data: ProjectDocuments): void {
  catalog.value = data
  selected.value = [...data.linked]
}

async function load(): Promise<void> {
  error.value = ''
  busy.value = 'load'
  try {
    const result = await api.codeDocuments(props.projectId)
    if (result.ok && result.data) update(result.data)
    else error.value = result.error ?? t('没能加载项目资料')
  } finally {
    busy.value = ''
  }
}

async function save(): Promise<void> {
  error.value = ''
  busy.value = 'save'
  try {
    const result = await api.codeSetDocuments(props.projectId, [...selected.value])
    if (result.ok && result.data) {
      update(result.data)
      report.value = null
    } else error.value = result.error ?? t('没存上')
  } finally {
    busy.value = ''
  }
}

async function ask(): Promise<void> {
  if (busy.value || dirty.value || !query.value.trim()) return
  error.value = ''
  report.value = null
  busy.value = 'ask'
  try {
    const result = await api.codeEvidence(props.projectId, query.value.trim())
    if (result.ok && result.data) report.value = result.data
    else error.value = result.error ?? t('项目问答未完成')
  } finally {
    busy.value = ''
  }
}

onMounted(load)
</script>

<template>
  <section class="project-evidence" :aria-label="t('项目资料与问答')">
    <span v-if="busy === 'load'" role="status">{{ t('正在加载项目资料…') }}</span>
    <div v-if="catalog">
      <details>
        <summary>{{ t('关联资料（{n} 份）', { n: catalog.linked.length }) }}</summary>
        <p class="hint">{{ t('从资料库选出这个项目可以检索的文件。') }}</p>
        <p v-if="!catalog.documents.length" class="hint">{{ t('先在「资料库」上传文件。') }}</p>
        <div v-else class="document-list">
          <label v-for="doc in catalog.documents" :key="doc.id">
            <input v-model="selected" type="checkbox" :value="doc.id" :disabled="!!busy" />
            {{ doc.name }}
          </label>
        </div>
        <div v-if="catalog.documents.length" class="actions">
          <button class="btn" type="button" :disabled="!dirty || !!busy" @click="save">{{ t('保存关联') }}</button>
          <button class="link" type="button" :disabled="!!busy" @click="load">{{ t('重新加载资料') }}</button>
        </div>
      </details>

      <form class="ask" @submit.prevent="ask">
        <label :for="`evidence-question-${projectId}`">{{ t('核对项目需求与代码') }}</label>
        <textarea :id="`evidence-question-${projectId}`" v-model="query" rows="2" maxlength="2000"
          :disabled="!!busy" :placeholder="t('例如：这个功能从界面到后端走哪几步？')" />
        <div class="actions">
          <button class="btn primary" type="submit" :disabled="!!busy || dirty || !query.trim()">{{ t('查依据') }}</button>
          <span v-if="busy === 'ask'" class="hint" role="status">{{ t('正在检索并整理依据…') }}</span>
          <span v-else-if="dirty" class="hint">{{ t('先保存资料关联再提问。') }}</span>
          <span v-else class="hint">{{ t('问题和命中的摘录会交给配置的对话模型。') }}</span>
        </div>
      </form>
    </div>
    <p v-if="error" class="msg error" role="alert">
      {{ error }} <button v-if="!catalog" class="link" type="button" @click="load">{{ t('重试') }}</button>
    </p>
    <div v-if="report" class="answer" aria-live="polite">
      <p v-if="report.status === 'empty'">{{ t('没有找到相关的项目资料或代码。') }}</p>
      <p v-else-if="report.status === 'sources_only'">{{ t('已找到依据，暂时无法整理出带引用的回答。') }}</p>
      <ul v-if="report.claims.length" class="claims">
        <li v-for="(claim, i) in report.claims" :key="i">
          {{ claim.text }}
          <button v-for="id in claim.sources" :key="id" type="button" class="link cite"
            :aria-label="t('查看依据 {id}', { id })" @click="evidence?.reveal(id)">{{ id }}</button>
        </li>
      </ul>
      <div v-if="report.gaps.length">
        <strong>{{ t('还缺的依据') }}</strong>
        <ul><li v-for="(gap, i) in report.gaps" :key="i">{{ gap }}</li></ul>
      </div>
      <template v-if="report.sources.length">
        <strong>{{ t('检索到的依据') }}</strong>
        <EvidenceSources ref="evidence" :sources="report.sources" />
      </template>
    </div>
  </section>
</template>

<style scoped>
.project-evidence {
  display: grid;
  gap: 12px;
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px solid var(--ui-line);
  overflow-wrap: anywhere;
}
summary {
  cursor: pointer;
}
.document-list {
  max-height: 180px;
  overflow-y: auto;
  display: grid;
  gap: 5px;
}
.document-list label {
  display: flex;
  align-items: baseline;
  gap: 6px;
}
.ask {
  display: grid;
  gap: 6px;
  margin: 10px 0 0;
}
.ask label {
  font-weight: 600;
}
.ask textarea {
  min-width: 0;
  padding: 6px 8px;
  border: 1px solid var(--ui-line);
  border-radius: var(--ui-r-s);
  background: var(--ui-field);
  color: var(--ui-text);
  font: inherit;
  resize: vertical;
}
.claims {
  padding-inline-start: 20px;
}
.claims li {
  margin-bottom: 6px;
}
.cite {
  margin-inline-start: 4px;
  font-family: var(--ui-mono);
}
</style>
