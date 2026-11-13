<script setup lang="ts">
import { onActivated, ref } from 'vue'
import type { AiSettings } from '../../../../shared/settings'
import { bridge } from '../../platform/bridge'
import { store } from '../store'

const api = bridge!
const ai = ref<AiSettings | null>(null)
const error = ref('')
const loading = ref(false)

const NAMES: Record<string, string> = { ears: '听（唤醒、识别）', tts: '说（语音合成）', embed: '记忆和资料库（向量模型）' }

function describe(state: string): { text: string; cls: string } {
  if (state === 'ready') return { text: '正常', cls: 'good' }
  if (state === 'loading') return { text: '加载中', cls: '' }
  if (state === 'off') return { text: '没启用', cls: '' }
  return { text: '出错了', cls: 'warn' }
}

async function load(): Promise<void> {
  loading.value = true
  const res = await api.aiSettings()
  loading.value = false
  ai.value = res.data ?? null
  error.value = res.ok ? '' : (res.error ?? '')
}

function reveal(key: 'env' | 'models' | 'data' | 'mcp'): void {
  api.revealPath(key)
}

onActivated(load)
</script>

<template>
  <h2>运行状态</h2>
  <p class="lead">艾拉由三部分组成：桌面上的她、这台电脑上的 AI 服务（aila-ai）、可选的同步服务器（aila-server）。</p>

  <div class="card">
    <h3>本地 AI 服务</h3>
    <div v-if="error" class="offline">{{ error }}</div>
    <template v-else-if="ai">
      <div class="row">
        <span class="label">大模型</span>
        <span>
          {{ ai.llm.model }}
          <span v-if="!ai.llm.apiKeySet" class="tag warn">没填密钥，用的是假模型</span>
        </span>
      </div>
      <div v-for="(state, key) in ai.status" :key="key" class="row">
        <span class="label">{{ NAMES[key] ?? key }}</span>
        <span>
          <span class="tag" :class="describe(state).cls">{{ describe(state).text }}</span>
          <span v-if="describe(state).cls === 'warn'" class="hint">{{ state }}</span>
        </span>
      </div>
    </template>
    <div class="actions">
      <button class="btn" :disabled="loading" @click="load">刷新</button>
    </div>
  </div>

  <div class="card">
    <h3>同步</h3>
    <div class="row">
      <span class="label">账号</span>
      <span v-if="store.account && store.account.status !== 'signed-out'">
        {{ store.account.email }}
        <span class="tag" :class="store.account.status === 'online' ? 'good' : 'warn'">
          {{ store.account.status === 'online' ? '已同步' : store.account.status === 'connecting' ? '连接中' : '离线' }}
        </span>
      </span>
      <span v-else class="hint" style="margin: 0">没登录，数据只在这台电脑上</span>
    </div>
  </div>

  <div v-if="ai" class="card">
    <h3>文件位置</h3>
    <div class="row">
      <span class="label">配置</span>
      <span><button class="link" @click="reveal('env')">{{ ai.paths.env }}</button></span>
    </div>
    <div class="row">
      <span class="label">模型</span>
      <span><button class="link" @click="reveal('models')">{{ ai.paths.models }}</button></span>
    </div>
    <div class="row">
      <span class="label">资料库数据</span>
      <span><button class="link" @click="reveal('data')">{{ ai.paths.data }}</button></span>
    </div>
    <div class="row">
      <span class="label">外部工具配置</span>
      <span><button class="link" @click="reveal('mcp')">{{ ai.paths.mcp }}</button></span>
    </div>
    <span class="hint">点击在资源管理器里打开所在的文件夹。</span>
  </div>
</template>
