<script setup lang="ts">
// 设置页可以维护余额。对话和任务共用同一条用量栏，点一家就在当前页展开。
import { computed, onActivated, onBeforeUnmount, onDeactivated, onMounted, ref } from 'vue'
import { summaryWindows, type ClaudeQuotaConfig, type ProviderQuota, type QuotaWindow } from '../../../../shared/providerQuotas'
import { lang, t } from '../../i18n'
import { bridge } from '../../platform/bridge'

const props = defineProps<{ compact?: boolean }>()
const emit = defineEmits<{ manage: [] }>()
const idPrefix = props.compact ? 'workspace-quota' : 'quota'
const detailsId = idPrefix + '-details'
const tabId = (id: string): string => idPrefix + '-tab-' + id

const providers = ref<ProviderQuota[]>([
  ['codex', 'Codex'], ['claude', 'Claude Code'], ['cursor', 'Cursor'], ['grok', 'Grok'], ['antigravity', 'Antigravity']
].map(([id, name]) => ({ id, name, status: 'waiting', windows: [], linked: false })))
const selected = ref('')
const active = computed(() => providers.value.find((p) => p.id === selected.value))
const busy = ref(false)
const error = ref('')
const now = ref(Date.now() / 1000)
const claudeConfig = ref<ClaudeQuotaConfig>()
const usePowerShell = ref(false)
const configText = computed(() => {
  if (!claudeConfig.value) return ''
  const { statusLine, powerShell } = claudeConfig.value
  return JSON.stringify({
    statusLine: { ...statusLine, command: usePowerShell.value && powerShell ? powerShell : statusLine.command },
  }, null, 2)
})
const form = ref<ReturnType<typeof newForm> | null>(null)
const locale = computed(() => lang.value === 'zh' ? 'zh-CN' : 'en-US')
const numbers = computed(() => new Intl.NumberFormat(locale.value, { maximumFractionDigits: 2 }))
const dates = computed(() => new Intl.DateTimeFormat(locale.value, { dateStyle: 'medium', timeStyle: 'short' }))

function newForm(window?: QuotaWindow) {
  let reset = ''
  if (window?.reset_at) {
    const date = new Date(window.reset_at * 1000)
    reset = new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16)
  }
  return {
    window: window?.id, name: '', label: window?.label ?? '', unit: window?.unit ?? '',
    remaining: window ? String(window.remaining) : '', total: String(window?.total ?? ''), reset,
  }
}

async function run(action: () => Promise<void>): Promise<void> {
  if (busy.value) return
  error.value = ''
  busy.value = true
  try {
    await action()
  } catch {
    error.value = t('用量操作失败，稍后再试。')
  } finally {
    busy.value = false
  }
}

async function load(refresh: boolean): Promise<void> {
  if (!bridge) return
  const api = bridge
  await run(async () => {
    const result = await api.providerQuotas(refresh)
    if (!result.ok || !result.data) throw new Error()
    providers.value = result.data
  })
}

function open(id: string): void {
  selected.value = selected.value === id ? '' : id
  form.value = id === 'new' && selected.value ? newForm() : null
}

function closeOnEscape(event: KeyboardEvent): void {
  if (!selected.value) return
  event.stopPropagation()
  document.getElementById(tabId(selected.value))?.focus()
  selected.value = ''
  form.value = null
}

function visibleWindows(provider: ProviderQuota): QuotaWindow[] {
  const windows = summaryWindows(provider.windows, now.value)
  return props.compact ? windows.slice(0, 1) : windows
}

async function save(): Promise<void> {
  if (!bridge || !form.value) return
  const api = bridge
  const input = form.value
  const provider = active.value?.id
  const remaining = Number(input.remaining)
  const total = input.total === '' ? (input.unit === '%' ? 100 : null) : Number(input.total)
  const reset_at = input.reset ? new Date(input.reset).getTime() / 1000 : null
  if (total !== null && remaining > total) {
    error.value = t('剩余量不能超过总量。')
    return
  }
  await run(async () => {
    const before = new Set(providers.value.map((p) => p.id))
    const result = await api.recordProviderQuota({
      provider, name: provider ? undefined : input.name, window: input.window,
      label: input.label || t('余额'), unit: input.unit, remaining, total, reset_at,
    })
    if (!result.ok || !result.data) throw new Error()
    providers.value = result.data
    if (!provider && selected.value === 'new') selected.value = result.data.find((p) => !before.has(p.id))?.id ?? ''
    if (form.value === input) form.value = null
  })
}

async function setupClaude(): Promise<void> {
  if (!bridge) return
  const api = bridge
  await run(async () => {
    const result = await api.claudeQuotaConfig()
    if (!result.ok || !result.data) throw new Error()
    claudeConfig.value = result.data
  })
}

async function linkCursor(event: Event): Promise<void> {
  if (!bridge) return
  const api = bridge
  const checkbox = event.target as HTMLInputElement
  const linked = checkbox.checked
  checkbox.checked = !linked
  await run(async () => {
    const result = await api.linkCursorQuota(linked)
    if (!result.ok || !result.data) throw new Error()
    providers.value = result.data
  })
}

function windowLabel(window: QuotaWindow): string {
  const mins = window.minutes
  if (!mins) return t(window.label)
  const period = mins % 1440 === 0 ? t('{n} 天', { n: mins / 1440 })
    : mins % 60 === 0 ? t('{n} 小时', { n: mins / 60 })
      : t('{n} 分钟', { n: mins })
  return t(window.label) + ' · ' + period
}

function amount(value: number, unit: string): string {
  const digits = value > 0 && value < 0.01 ? '<0.01' : numbers.value.format(value)
  return unit === '%' ? digits + '%' : digits + ' ' + t(unit)
}

function until(at: number): string {
  const minutes = Math.max(1, Math.ceil((at - now.value) / 60))
  if (minutes < 60) return t('{n} 分钟', { n: minutes })
  const hours = Math.floor(minutes / 60)
  if (hours >= 24) return t('{d} 天 {h} 小时', { d: Math.floor(hours / 24), h: hours % 24 })
  return t('{h} 小时 {m} 分', { h: hours, m: minutes % 60 })
}

function resetStatus(window: QuotaWindow): string {
  if (window.reset_at === null) return t('重置时间未知')
  return window.reset_at > now.value ? t('{time}后重置', { time: until(window.reset_at) }) : t('已到重置时间，待更新')
}

function estimateStatus(window: QuotaWindow): string {
  if (window.stale || (window.reset_at !== null && window.reset_at <= now.value)) return t('更新用量后重新预测')
  if (window.remaining === 0) return t('已用完')
  if (!window.forecast) return t('记录到用量变化后预测')
  if (!window.forecast.within_cycle) return t('按当前速度可用到重置')
  if (window.forecast.at <= now.value) return t('已到预计用完时间，待更新')
  return t('按当前速度约 {time}后用完', { time: until(window.forecast.at) })
}

function status(provider: ProviderQuota): string {
  switch (provider.status) {
    case 'manual': return t('手动录入')
    case 'missing_cli': return t('未检测到本机 CLI')
    case 'unavailable': return t('本次未读到用量')
    case 'ready': return ''
    default: return t('等待用量数据')
  }
}

let ticker: ReturnType<typeof setInterval> | undefined
let poller: ReturnType<typeof setInterval> | undefined
let activeView = false
let unlistenService: (() => void) | undefined
async function start(): Promise<void> {
  if (activeView) return
  activeView = true
  now.value = Date.now() / 1000
  ticker = setInterval(() => { now.value = Date.now() / 1000 }, 15000)
  poller = setInterval(() => { if (!document.hidden) void load(true) }, 300000)
  unlistenService = bridge?.onServiceState?.((state) => {
    if (activeView && error.value && (state.phase === 'ready' || state.phase === 'external')) void load(true)
  })
  await load(false)
  if (activeView) await load(true)
}
function stop(): void {
  activeView = false
  unlistenService?.()
  unlistenService = undefined
  clearInterval(ticker)
  clearInterval(poller)
}
onMounted(start)
onActivated(start)
onDeactivated(stop)
onBeforeUnmount(stop)
</script>

<template>
  <section :class="compact ? 'quota-card compact' : 'card quota-card'" :aria-label="t('平台用量与积分')" @keydown.esc="closeOnEscape">
    <header v-if="!compact" class="quota-head">
      <h3>{{ t('平台用量与积分') }}</h3>
      <div class="quota-actions">
        <button class="btn" type="button" :disabled="busy" @click="load(true)">
          {{ busy ? t('正在更新…') : t('刷新用量') }}
        </button>
        <button :id="tabId('new')" class="btn" type="button" :aria-expanded="selected === 'new'"
          :aria-controls="selected === 'new' ? detailsId : undefined" @click="open('new')">
          {{ t('添加平台') }}
        </button>
      </div>
    </header>

    <div class="quota-grid">
      <button v-for="p in providers" :id="tabId(p.id)" :key="p.id" class="quota-tile" type="button"
        :title="compact ? p.name : undefined" :aria-expanded="selected === p.id"
        :aria-controls="selected === p.id ? detailsId : undefined" @click="open(p.id)">
        <span class="quota-brand"><strong>{{ p.name }}</strong><span aria-hidden="true">{{ selected === p.id ? '▴' : '▾' }}</span></span>
        <template v-if="p.windows.length">
          <span v-for="w in visibleWindows(p)" :key="w.source + w.id" class="quota-tile-window"
            :class="{ stale: w.stale || (w.reset_at !== null && w.reset_at <= now) }">
            <small v-if="!compact && p.windows.length > 1">{{ windowLabel(w) }}</small>
            <b>{{ amount(w.remaining, w.unit) }}</b>
            <span class="hint">{{ resetStatus(w) }}</span>
          </span>
          <small v-if="p.status === 'unavailable' || p.status === 'missing_cli' || p.windows.some((w) => w.stale)" class="quota-read-note">
            {{ t('含较早记录') }}
          </small>
        </template>
        <span v-else class="hint">{{ status(p) }}</span>
      </button>
    </div>

    <section v-if="selected" :id="detailsId" class="quota-details" :aria-labelledby="tabId(selected)">
      <div class="quota-window-head">
        <h4>{{ active?.name ?? t('添加平台') }}</h4>
        <div v-if="compact" class="quota-actions">
          <button class="btn" type="button" :disabled="busy" @click="load(true)">
            {{ busy ? t('正在更新…') : t('刷新用量') }}
          </button>
          <button class="btn" type="button" @click="emit('manage')">{{ t('管理额度') }}</button>
        </div>
      </div>
      <p v-if="active && status(active)" class="hint">{{ status(active) }}</p>
      <div v-if="active?.windows.length" class="quota-windows">
        <article v-for="w in active.windows" :key="w.source + w.id" class="quota-window">
          <div class="quota-window-head">
            <strong>{{ windowLabel(w) }}</strong>
            <b>{{ amount(w.remaining, w.unit) }}<template v-if="w.total !== null && w.unit !== '%'"> / {{ amount(w.total, w.unit) }}</template></b>
          </div>
          <progress v-if="w.total !== null" :value="w.remaining" :max="w.total" :aria-label="t('{window}剩余量', { window: windowLabel(w) })"></progress>
          <div class="quota-meta">
            <span :title="w.reset_at ? dates.format(w.reset_at * 1000) : undefined">{{ resetStatus(w) }}</span>
            <span>{{ estimateStatus(w) }}</span>
          </div>
          <div class="quota-meta">
            <span>{{ w.source === 'manual' ? t('手动录入') : w.source === 'shared' ? t('来自 Grok 共享额度') : t('本机读取') }}
              · {{ dates.format(w.observed_at * 1000) }}<template v-if="w.stale"> · {{ t('较早记录') }}</template>
            </span>
            <button v-if="!compact && w.source === 'manual'" class="btn small-btn" type="button" :disabled="busy" @click="form = newForm(w)">{{ t('更新记录') }}</button>
          </div>
        </article>
      </div>
      <template v-if="!compact && active?.id === 'claude'">
        <button v-if="!claudeConfig" class="btn" type="button" :disabled="busy" @click="setupClaude">{{ t('连接 Claude Code 状态栏') }}</button>
        <div v-else class="quota-setup">
          <p class="hint">{{ t('把下面的 statusLine 合并到 Claude Code 的 settings.json，再完成一轮对话后刷新。已有状态栏时，可在原脚本中把同一份输入传给采集命令。') }}</p>
          <label v-if="claudeConfig.powerShell" class="quota-link">
            <input v-model="usePowerShell" type="checkbox" />
            {{ t('这台电脑没装 Git Bash，用 PowerShell 配置') }}
          </label>
          <textarea readonly rows="5" :value="configText" :aria-label="t('Claude Code 状态栏配置')" @focus="($event.target as HTMLTextAreaElement).select()"></textarea>
        </div>
      </template>
      <template v-if="!compact && active?.id === 'cursor'">
        <label class="quota-link">
          <input type="checkbox" :checked="active.linked" :disabled="busy" @change="linkCursor" />
          {{ t('我的 Cursor 与 Grok 使用同一个 X 订阅') }}
        </label>
        <p class="hint">{{ t('关联后会显示 Grok 确认的共享额度。独立 Cursor 套餐可以在这里录入。') }}</p>
      </template>
      <button v-if="!compact && active && !form" class="btn" type="button" @click="form = newForm()">{{ t('录入余额') }}</button>
      <form v-if="form" class="quota-form" @submit.prevent="save">
        <label v-if="!active">{{ t('平台名称') }}<input v-model.trim="form.name" required maxlength="60" /></label>
        <label>{{ t('额度名称') }}<input v-model.trim="form.label" :placeholder="t('余额')" maxlength="60" /></label>
        <label>{{ t('单位') }}<input v-model.trim="form.unit" :placeholder="t('积分、% 或金额单位')" required maxlength="16" /></label>
        <label>{{ t('剩余量') }}<input v-model="form.remaining" type="number" required min="0" max="1000000000000" step="any" /></label>
        <label>{{ t('总量（选填）') }}<input v-model="form.total" type="number" min="0.000001" max="1000000000000" step="any" /></label>
        <label>{{ t('重置时间（选填）') }}<input v-model="form.reset" type="datetime-local" /></label>
        <div class="quota-actions">
          <button class="btn primary" type="submit" :disabled="busy">{{ t('保存记录') }}</button>
          <button v-if="active" class="btn" type="button" @click="form = null">{{ t('取消') }}</button>
        </div>
      </form>
    </section>
    <p v-if="error" class="msg error" role="alert">{{ error }}</p>
  </section>
</template>

<style scoped>
.quota-head, .quota-window-head, .quota-brand, .quota-meta, .quota-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.quota-head, .quota-meta, .quota-actions { flex-wrap: wrap; }
.quota-head h3 { margin: 0; }
.quota-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 9rem), 1fr));
  gap: 8px;
  margin-top: 12px;
}
.quota-tile {
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 6px;
  padding: 10px;
  min-height: 86px;
  background: var(--ui-surface, transparent);
  color: inherit;
  border: 1px solid var(--ui-line, #7776);
  border-radius: 8px;
  text-align: left;
  cursor: pointer;
  overflow-wrap: anywhere;
}
.quota-tile[aria-expanded="true"] { border-color: var(--accent, #5d6bd6); }
.quota-tile:focus-visible { outline: 2px solid var(--accent, #5d6bd6); outline-offset: 2px; }
.quota-tile-window { display: grid; gap: 1px; }
.quota-tile-window b, .quota-window-head b { font-variant-numeric: tabular-nums; }
.quota-tile-window .hint { margin: 0; }
.quota-tile-window.stale b { font-weight: normal; }
.quota-details { margin-top: 12px; padding: 12px; border: 1px solid var(--ui-line, #7776); border-radius: 8px; }
.quota-details h4 { margin: 0 0 8px; }
.quota-window {
  border-top: 1px solid var(--ui-line, #7776);
  padding: 10px 0;
  min-width: 0;
  overflow-wrap: anywhere;
}
.quota-window progress {
  appearance: none;
  width: 100%;
  height: 7px;
  border: 0;
  border-radius: 999px;
  overflow: hidden;
  background: var(--ui-raised);
  accent-color: var(--ui-accent);
}
.quota-window progress::-webkit-progress-bar { background: var(--ui-raised); }
.quota-window progress::-webkit-progress-value { background: var(--ui-accent); }
.quota-window-head { flex-wrap: wrap; }
.quota-meta { font-size: 12px; margin-top: 4px; }
.quota-setup textarea { width: 100%; box-sizing: border-box; }
.quota-link { display: flex; align-items: baseline; gap: 8px; margin-top: 6px; }
.quota-form {
  margin-top: 10px;
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 10rem), 1fr));
  gap: 8px;
}
.quota-form label { display: grid; gap: 4px; min-width: 0; }
.quota-form input { min-width: 0; width: 100%; box-sizing: border-box; }
.quota-form .quota-actions { grid-column: 1 / -1; justify-content: flex-start; }
.small-btn { padding: 2px 8px; }
.compact {
  flex: none;
  padding: 4px 10px;
  border-bottom: 1px solid var(--ui-line);
  background: var(--ui-panel);
  color: var(--ui-text);
}
.compact .quota-grid {
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 7.5rem), 1fr));
  gap: 4px;
  margin: 0;
  padding: 2px;
  max-height: 100px;
  overflow: auto;
  scrollbar-width: thin;
}
.compact .quota-tile {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-content: center;
  min-height: 44px;
  gap: 1px 5px;
  padding: 4px 7px;
  font: 12px/1.25 var(--ui-font);
}
.compact .quota-brand { grid-area: 1 / 1; justify-content: flex-start; gap: 1px; min-width: 0; }
.compact .quota-brand strong { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.compact .quota-brand > span { font-size: 8px; }
.compact .quota-tile-window { display: contents; }
.compact .quota-tile-window b {
  grid-area: 1 / 2;
  max-width: 5em;
  text-align: right;
}
.compact .quota-tile .hint, .compact .quota-read-note {
  grid-column: 1 / -1;
  font-size: 11px;
}
.compact .quota-tile[aria-expanded="true"] {
  background: var(--ui-accent-soft);
  border-color: var(--ui-accent-line);
}
.compact .quota-tile:focus-visible { outline: 2px solid var(--ui-accent); outline-offset: -2px; }
.compact .hint { color: var(--ui-text-2); }
.compact .quota-details {
  margin-top: 4px;
  padding: 5px 8px;
  max-height: min(30vh, 220px);
  overflow: auto;
  scrollbar-width: thin;
}
.compact .quota-details h4 { margin: 0; font-size: 13px; }
.compact .quota-details p.hint { margin: 4px 0; font-size: 12px; }
.compact .quota-windows {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 17rem), 1fr));
  gap: 0 12px;
  margin-top: 4px;
}
.compact .quota-window { padding: 5px 0; font-size: 12px; }
.compact .quota-window progress { display: block; height: 5px; margin: 3px 0; }
.compact .quota-meta { margin-top: 2px; gap: 2px 6px; font-size: 11px; }
.compact .quota-actions .btn {
  min-height: 24px;
  padding: 2px 6px;
  border: 1px solid var(--ui-line-strong);
  border-radius: var(--ui-r-s);
  font: inherit;
  font-size: 12px;
  color: var(--ui-text);
  background: var(--ui-surface);
  cursor: pointer;
}
.compact .quota-actions .btn:disabled { color: var(--ui-muted); cursor: default; }
.compact .quota-actions .btn:focus-visible { outline: 2px solid var(--ui-accent); outline-offset: 1px; }
.compact .msg.error { margin: 6px 2px 0; color: var(--ui-bad-text); font-size: 12px; }
</style>
