<script setup lang="ts">
import { onActivated, ref } from 'vue'
import type { KnowledgeDoc } from '../../../../shared/cloud'
import { bridge } from '../../platform/bridge'
import { store } from '../store'

const api = bridge!
const docs = ref<KnowledgeDoc[]>([])
const offline = ref(false)
const errors = ref<string[]>([])
const adding = ref(false)

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
  return d.chars >= 10000 ? `${(d.chars / 10000).toFixed(1)} 万字` : `${d.chars} 字`
}

function date(iso: string): string {
  const d = new Date(iso)
  return `${d.getMonth() + 1} 月 ${d.getDate()} 日`
}

onActivated(load)
</script>

<template>
  <h2>资料库</h2>
  <p class="lead">放进来的文档、笔记，问她的时候她会去翻，回答时告诉你出自哪份资料。文件只保存在这台电脑上。</p>

  <div v-if="offline" class="offline">
    连不上 AI 服务，请先启动 aila-ai。
    <button class="link" @click="load">重试</button>
  </div>

  <div v-else class="card">
    <table v-if="docs.length" class="list">
      <tr v-for="d in docs" :key="d.id">
        <td class="name">{{ d.name }}</td>
        <td class="meta">{{ size(d) }} · {{ d.chunks }} 段</td>
        <td class="meta">{{ date(d.addedAt) }}</td>
        <td class="del"><button class="x" title="从资料库删掉" @click="remove(d.id)">×</button></td>
      </tr>
    </table>
    <p v-else class="empty">还没有资料。支持 txt、md、pdf、docx、html，单个文件最大 20MB。</p>

    <p v-for="e in errors" :key="e" class="msg error">{{ e }}</p>
    <div class="actions">
      <button class="btn primary" :disabled="adding" @click="add">
        {{ adding ? store.knowledgeBusy || '选择文件……' : '添加资料…' }}
      </button>
      <span class="hint">扫描版 PDF（图片）要先做文字识别才能读。</span>
    </div>
  </div>
</template>

<style scoped>
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
