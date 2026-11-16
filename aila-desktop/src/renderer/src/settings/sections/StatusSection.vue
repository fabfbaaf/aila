<script setup lang="ts">
import { computed, onActivated, onDeactivated, ref } from 'vue'
import type { AiSettings } from '../../../../shared/settings'
import type { ServiceLog, ServiceState } from '../../../../shared/service'
import { bridge } from '../../platform/bridge'
import { store } from '../store'

const api = bridge!
const ai = ref<AiSettings | null>(null)
const error = ref('')
const loading = ref(false)
const service = ref<ServiceState>({ phase: 'checking', pid: null, error: '' })
const restarting = ref(false)
const serviceError = ref('')
const showLogs = ref(false)
const logs = ref<ServiceLog[]>([])
const logsError = ref('')
let unsubscribe: (() => void) | undefined
let logTimer: ReturnType<typeof setInterval> | undefined
let activation = 0
const serviceLabel = computed(() => ({
  checking: '启动中', starting: '启动中', ready: '正常', external: '别人开的',
  crashed: '停了，正在重启', failed: '停了', missing: '找不到', stopped: '停了'
})[service.value.phase])

async function loadLogs(): Promise<void> {
  try {
    const result = await api.serviceLogs()
    logs.value = (result.data ?? []).slice(-200)
    logsError.value = result.ok ? '' : result.error ?? '日志没读到，再试一次。'
  } catch { logsError.value = '日志没读到，再试一次。' }
}

async function toggleLogs(): Promise<void> {
  showLogs.value = !showLogs.value
  if (showLogs.value) await loadLogs()
}

async function restartService(): Promise<void> {
  restarting.value = true
  serviceError.value = ''
  try {
    const result = await api.serviceRestart()
    serviceError.value = result.ok ? '' : result.error ?? '服务没能重启，看看日志。'
  } catch { serviceError.value = '服务没能重启，看看日志。' }
  finally { restarting.value = false }
}

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

onActivated(async () => {
  const current = ++activation
  unsubscribe = api.onServiceState((state) => {
    service.value = state
    if (state.phase === 'ready' || state.phase === 'external') void load()
  })
  try {
    const state = await api.serviceStatus()
    if (current !== activation) return
    service.value = state
    serviceError.value = ''
  } catch {
    if (current !== activation) return
    serviceError.value = '服务状态没读到，切回这一页再试一次。'
  }
  if (current !== activation) return
  void load()
  logTimer = setInterval(() => { if (showLogs.value) void loadLogs() }, 2000)
})
onDeactivated(() => { ++activation; unsubscribe?.(); clearInterval(logTimer) })
</script>

<template>
  <h2>运行状态</h2>
  <p class="lead">艾拉由三部分组成：桌面上的她、这台电脑上的 AI 服务（aila-ai）、可选的同步服务器（aila-server）。</p>

  <div class="card">
    <h3>AI 服务</h3>
    <div class="row">
      <span class="label">状态</span>
      <span role="status" aria-atomic="true" class="tag" :class="{ good: service.phase === 'ready', warn: ['failed', 'missing', 'crashed'].includes(service.phase) }">{{ serviceLabel }}</span>
    </div>
    <div v-if="service.pid" class="row"><span class="label">进程号</span><span>{{ service.pid }}</span></div>
    <p v-if="service.phase === 'external'" class="hint">服务是从别处启动的，退出艾拉时会保留它。要重启的话，去原来的终端操作。</p>
    <p v-if="service.error || serviceError" class="offline" role="alert">{{ serviceError || service.error }}</p>
    <div class="actions">
      <button class="btn" :disabled="restarting || ['external', 'checking', 'starting'].includes(service.phase)" @click="restartService">{{ restarting ? '正在重启…' : '重启服务' }}</button>
      <button class="btn" :aria-expanded="showLogs" aria-controls="service-logs" @click="toggleLogs">{{ showLogs ? '收起日志' : '看日志' }}</button>
    </div>
    <div v-if="showLogs" id="service-logs" class="service-logs">
      <p class="hint">最近 200 行，每两秒更新。</p>
      <p v-if="logsError" class="offline" role="alert">{{ logsError }}</p>
      <pre tabindex="0" aria-label="AI 服务最近的日志">{{ logs.length ? logs.map(line => `[${new Date(line.time).toLocaleTimeString()}] ${line.text}`).join('\n') : '还没有日志。' }}</pre>
    </div>
  </div>

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

<style scoped>
.service-logs { min-width: 0; margin-top: 16px; }
.service-logs pre { max-height: 320px; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; font: 12px/1.6 Consolas, monospace; padding: 12px; background: var(--bg); border: 1px solid var(--field-line); border-radius: 8px; }
.service-logs pre:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
</style>
