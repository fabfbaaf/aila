<script setup lang="ts">
import { t, dateText, lang } from '../../i18n'
import { onActivated, onMounted, ref } from 'vue'
import type { KnowledgeDoc } from '../../../../shared/cloud'
import { bridge } from '../../platform/bridge'
import { store } from '../store'
import type { DocumentSource } from '../../../../shared/evidence'
import EvidenceSources from '../../components/EvidenceSources.vue'

const api = bridge!
const docs = ref<KnowledgeDoc[]>([])
const offline = ref(false)
const errors = ref<string[]>([])
const adding = ref(false)
const query = ref('')
const searching = ref(false)
const searched = ref<null | DocumentSource[]>(null)
const searchError = ref('')

async function inspect(): Promise<void> {
  if (searching.value || !query.value.trim()) return
  searching.value = true
  searched.value = null
  searchError.value = ''
  try {
    const result = await api.knowledgeSearch(query.value.trim())
    if (result.ok && result.data) searched.value = result.data.map((hit, i) => ({ ...hit, id: 'd' + (i + 1) }))
    else searchError.value = result.error ?? t('检索检查未完成')
  } finally {
    searching.value = false
  }
}

async function load(): Promise<void> {
  try {
    docs.value = await api.knowledgeList()
    offline.value = false
  } catch {
    offline.value = true
  }
}

async function add(): Promise<void> {
  errors.value = []
  adding.value = true
  const res = await api.knowledgeAdd()
  adding.value = false
  store.knowledgeBusy = ''
  if (!res) return // 取消了
  errors.value = res.errors
  await load()
}

async function remove(id: string): Promise<void> {
  await api.knowledgeDelete(id)
  await load()
}

function size(d: KnowledgeDoc): string {
  // 中文按「万」算，英文按「k」算，数字不一样，分开写
  if (d.chars >= 10000 && lang.value === 'en') return t('{n} 字', { n: `${Math.round(d.chars / 1000)}k` })
  return d.chars >= 10000 ? t('{n} 万字', { n: (d.chars / 10000).toFixed(1) }) : t('{n} 字', { n: d.chars })
}

function date(iso: string): string {
  const d = new Date(iso)
  return dateText(d)
}

let loadingVisible = false
function loadVisible(): void {
  if (loadingVisible) return
  loadingVisible = true
  void load().finally(() => { loadingVisible = false })
}

// 资料页是异步加载的，初次进入时组件可能晚于父页面的激活。
onMounted(loadVisible)
onActivated(loadVisible)
</script>

<template>
  <h2>{{ t('资料库') }}</h2>
  <p class="lead">{{ t('放进来的文档、笔记，问她的时候她会去翻，回答时告诉你出自哪份资料。文件只保存在这台电脑上。') }}</p>

  <div v-if="offline" class="offline">
    {{ t('连不上 AI 服务，请先启动 aila-ai。') }}
    <button class="link" @click="load">{{ t('重试') }}</button>
  </div>

  <div v-else class="card">
    <table v-if="docs.length" class="list">
      <tr v-for="d in docs" :key="d.id">
        <td class="name">{{ d.name }}</td>
        <td class="meta">{{ size(d) }} · {{ t('{n} 段', { n: d.chunks }) }}</td>
        <td class="meta">{{ date(d.addedAt) }}</td>
        <td class="del"><button class="x" :title="t('从资料库删掉')" @click="remove(d.id)">×</button></td>
      </tr>
    </table>
    <p v-else class="empty">{{ t('还没有资料。支持 txt、md、pdf、docx、html，单个文件最大 20MB。') }}</p>

    <p v-for="e in errors" :key="e" class="msg error">{{ e }}</p>
    <div class="actions">
      <button class="btn primary" :disabled="adding" @click="add">
        {{ adding ? store.knowledgeBusy || t('选择文件……') : t('添加资料…') }}
      </button>
      <span class="hint">{{ t('扫描版 PDF（图片）要先做文字识别才能读。') }}</span>
    </div>
  </div>

  <details v-if="!offline && docs.length" class="card inspect">
    <summary>{{ t('检索检查') }}</summary>
    <form class="actions" @submit.prevent="inspect">
      <input v-model="query" maxlength="500" :aria-label="t('输入资料检索问题')" :placeholder="t('输入资料检索问题')" />
      <button class="btn" type="submit" :disabled="searching || !query.trim()">{{ t('查资料') }}</button>
    </form>
    <p class="hint">{{ t('按资料库的排序显示摘录，这一步只在本机运行。') }}</p>
    <p v-if="searchError" class="msg error" role="alert">{{ searchError }}</p>
    <p v-else-if="searching" role="status">{{ t('正在查资料…') }}</p>
    <p v-else-if="searched && !searched.length">{{ t('没有查到相关资料。') }}</p>
    <EvidenceSources v-else-if="searched" :sources="searched" />
  </details>
</template>

<style scoped>
.inspect summary {
  cursor: pointer;
}
.inspect input {
  flex: 1;
  min-width: 120px;
}
.name {
  word-break: break-all;
}

.meta {
  width: 1%;
  color: var(--muted);
  white-space: nowrap;
}

.del {
  width: 1%;
}
</style>
