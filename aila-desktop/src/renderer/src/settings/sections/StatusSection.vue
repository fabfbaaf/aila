<script setup lang="ts">
import { t } from '../../i18n'
import { computed, onActivated, onDeactivated, ref } from 'vue'
import type { AiSettings } from '../../../../shared/settings'
import type { ServiceLog, ServiceState } from '../../../../shared/service'
import type { UpdateInfo } from '../../../../shared/update'
import Toggle from '../Toggle.vue'
import { bridge } from '../../platform/bridge'
import { store } from '../store'

const api = bridge!
const ai = ref<AiSettings | null>(null)
const error = ref('')
const loading = ref(false)
const service = ref<ServiceState>({ phase: 'checking', pid: null, error: '' })
const restarting = ref(false)
const serviceError = ref('')

// ---------- 版本和更新（shared/update.ts） ----------

const update = ref<UpdateInfo | null>(null)
const checking = ref(false)
const checkAuto = ref(true)

async function loadUpdate(): Promise<void> {
  update.value = await api.updateLast()
  const s = await api.desktopStatus()
  if (s) checkAuto.value = s.prefs.checkUpdates
}

async function checkUpdate(): Promise<void> {
  checking.value = true
  try {
    update.value = await api.updateCheck()
  } finally {
    checking.value = false
  }
}

async function setAuto(v: boolean): Promise<void> {
  checkAuto.value = v
  const s = await api.desktopSetPrefs({ checkUpdates: v })
  if (s) checkAuto.value = s.prefs.checkUpdates
}
const showLogs = ref(false)
const logs = ref<ServiceLog[]>([])
const logsError = ref('')
let unsubscribe: (() => void) | undefined
let logTimer: ReturnType<typeof setInterval> | undefined
let activation = 0
const serviceLabel = computed(() => ({
  checking: t('启动中'), starting: t('启动中'), ready: t('正常'), external: t('别人开的'),
  crashed: t('停了，正在重启'), failed: t('停了'), missing: t('找不到'), stopped: t('停了')
})[service.value.phase])

async function loadLogs(): Promise<void> {
  try {
    const result = await api.serviceLogs()
    logs.value = (result.data ?? []).slice(-200)
    logsError.value = result.ok ? '' : result.error ?? t('日志没读到，再试一次。')
  } catch { logsError.value = t('日志没读到，再试一次。') }
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
    serviceError.value = result.ok ? '' : result.error ?? t('服务没能重启，看看日志。')
  } catch { serviceError.value = t('服务没能重启，看看日志。') }
  finally { restarting.value = false }
}

const NAMES: Record<string, string> = { ears: '听（唤醒、识别）', tts: '说（语音合成）', embed: '记忆和资料库（向量模型）' }

function describe(state: string): { text: string; cls: string } {
  if (state === 'ready') return { text: t('正常'), cls: 'good' }
  if (state === 'loading') return { text: t('加载中'), cls: '' }
  if (state === 'off') return { text: t('没启用'), cls: '' }
  if (state === 'idle') return { text: t('用到时再加载'), cls: '' }
  return { text: t('出错了'), cls: 'warn' }
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
  void loadUpdate()
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
    serviceError.value = t('服务状态没读到，切回这一页再试一次。')
  }
  if (current !== activation) return
  void load()
  logTimer = setInterval(() => { if (showLogs.value) void loadLogs() }, 2000)
})
onDeactivated(() => { ++activation; unsubscribe?.(); clearInterval(logTimer) })
</script>

<template>
  <h2>{{ t('运行状态') }}</h2>
  <p class="lead">{{ t('艾拉由三部分组成：桌面上的她、这台电脑上的 AI 服务（aila-ai）、可选的同步服务器（aila-server）。') }}</p>

  <div class="card">
    <h3>{{ t('版本和更新') }}</h3>
    <div class="row">
      <span class="label">{{ t('现在的版本') }}</span>
      <div class="inline">
        <span>{{ update?.current ?? '…' }}</span>
        <button class="btn" :disabled="checking" @click="checkUpdate">{{ checking ? t('在查……') : t('检查更新') }}</button>
        <template v-if="update?.checkedAt && !checking">
          <span v-if="update.error" :class="update.error.startsWith(t('还没有')) ? 'hint' : 'warn-text'" role="status">{{ update.error }}</span>
          <span v-else-if="!update.newer" class="hint">{{ t('已经是最新的了。') }}</span>
        </template>
      </div>
    </div>
    <div v-if="update?.newer" class="row">
      <span class="label">{{ t('新版本') }}</span>
      <div>
        <div class="inline">
          <span class="tag good">{{ update.latest }}</span>
          <button class="btn primary" @click="api.updateOpen(update.installer || update.page)">{{ t('下载') }}</button>
          <button class="link" @click="api.updateOpen(update.page)">{{ t('看看改了什么') }}</button>
        </div>
        <pre v-if="update.notes" class="notes">{{ update.notes }}</pre>
      </div>
    </div>
    <div class="row">
      <span class="label">{{ t('自动检查') }}</span>
      <div>
        <Toggle :model-value="checkAuto" @update:model-value="setAuto" />
        <span class="hint">{{ t('每天看一次 GitHub 上有没有新版本，有的话用系统通知提醒一次。只发一个不带你任何信息的请求；没有签名证书，所以下载、安装要你自己点。') }}</span>
      </div>
    </div>
  </div>

  <div class="card">
    <h3>{{ t('AI 服务') }}</h3>
    <div class="row">
      <span class="label">{{ t('状态') }}</span>
      <span role="status" aria-atomic="true" class="tag" :class="{ good: service.phase === 'ready', warn: ['failed', 'missing', 'crashed'].includes(service.phase) }">{{ serviceLabel }}</span>
    </div>
    <div v-if="service.pid" class="row"><span class="label">{{ t('进程号') }}</span><span>{{ service.pid }}</span></div>
    <p v-if="service.phase === 'external'" class="hint">{{ t('服务是从别处启动的，退出艾拉时会保留它。要重启的话，去原来的终端操作。') }}</p>
    <p v-if="service.error || serviceError" class="offline" role="alert">{{ serviceError || service.error }}</p>
    <div class="actions">
      <button class="btn" :disabled="restarting || ['external', 'checking', 'starting'].includes(service.phase)" @click="restartService">{{ restarting ? t('正在重启…') : t('重启服务') }}</button>
      <button class="btn" :aria-expanded="showLogs" aria-controls="service-logs" @click="toggleLogs">{{ showLogs ? t('收起日志') : t('看日志') }}</button>
    </div>
    <div v-if="showLogs" id="service-logs" class="service-logs">
      <p class="hint">{{ t('最近 200 行，每两秒更新。') }}</p>
      <p v-if="logsError" class="offline" role="alert">{{ logsError }}</p>
      <pre tabindex="0" :aria-label="t('AI 服务最近的日志')">{{ logs.length ? logs.map(line => `[${new Date(line.time).toLocaleTimeString()}] ${line.text}`).join('\n') : t('还没有日志。') }}</pre>
    </div>
  </div>

  <div class="card">
    <h3>{{ t('本地 AI 服务') }}</h3>
    <div v-if="error" class="offline">{{ error }}</div>
    <template v-else-if="ai">
      <div class="row">
        <span class="label">{{ t('大模型') }}</span>
        <span>
          {{ ai.llm.model }}
          <span v-if="!ai.llm.apiKeySet" class="tag warn">{{ t('没填密钥，用的是假模型') }}</span>
        </span>
      </div>
      <div v-for="(state, key) in ai.status" :key="key" class="row">
        <span class="label">{{ NAMES[key] ? t(NAMES[key]) : key }}</span>
        <span>
          <span class="tag" :class="describe(state).cls">{{ describe(state).text }}</span>
          <span v-if="describe(state).cls === 'warn'" class="hint">{{ state }}</span>
        </span>
      </div>
    </template>
    <div class="actions">
      <button class="btn" :disabled="loading" @click="load">{{ t('刷新') }}</button>
    </div>
  </div>

  <div class="card">
    <h3>{{ t('同步') }}</h3>
    <div class="row">
      <span class="label">{{ t('账号') }}</span>
      <span v-if="store.account && store.account.status !== 'signed-out'">
        {{ store.account.email }}
        <span class="tag" :class="store.account.status === 'online' ? 'good' : 'warn'">
          {{ store.account.status === 'online' ? t('已同步') : store.account.status === 'connecting' ? t('连接中') : t('离线') }}
        </span>
      </span>
      <span v-else class="hint" style="margin: 0">{{ t('没登录，数据只在这台电脑上') }}</span>
    </div>
  </div>

  <div v-if="ai" class="card">
    <h3>{{ t('文件位置') }}</h3>
    <div class="row">
      <span class="label">{{ t('配置') }}</span>
      <span><button class="link" @click="reveal('env')">{{ ai.paths.env }}</button></span>
    </div>
    <div class="row">
      <span class="label">{{ t('模型') }}</span>
      <span><button class="link" @click="reveal('models')">{{ ai.paths.models }}</button></span>
    </div>
    <div class="row">
      <span class="label">{{ t('资料库数据') }}</span>
      <span><button class="link" @click="reveal('data')">{{ ai.paths.data }}</button></span>
    </div>
    <div class="row">
      <span class="label">{{ t('外部工具配置') }}</span>
      <span><button class="link" @click="reveal('mcp')">{{ ai.paths.mcp }}</button></span>
    </div>
    <span class="hint">{{ t('点击在资源管理器里打开所在的文件夹。') }}</span>
  </div>
</template>

<style scoped>
.service-logs { min-width: 0; margin-top: 16px; }
.service-logs pre { max-height: 320px; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; font: 12px/1.6 Consolas, monospace; padding: 12px; background: var(--bg); border: 1px solid var(--field-line); border-radius: 8px; }
.service-logs pre:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }

.inline {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
}

.warn-text {
  color: var(--warn, #fbbf24);
  font-size: 13px;
}

.notes {
  max-height: 180px;
  margin: 8px 0 0;
  overflow: auto;
  white-space: pre-wrap;
  color: var(--muted);
  font: inherit;
  font-size: 12.5px;
}
</style>
