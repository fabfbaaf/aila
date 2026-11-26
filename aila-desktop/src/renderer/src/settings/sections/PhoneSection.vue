<script setup lang="ts">
/**
 * 设置 → 手机：同一个 Wi-Fi 下用手机和她聊天、看任务、开任务（AI 服务那边的门：aila-ai/app/phone）。
 * 默认关着；打开后给地址和 6 位配对码，配过的手机在这里看、删。同意她改文件、跑命令还是只能在电脑上点。
 */
import { t } from '../../i18n'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import type { AiResult, PhoneStatus } from '../../../../shared/settings'
import { bridge } from '../../platform/bridge'

const status = ref<PhoneStatus | null>(null)
const busy = ref(false)
const error = ref('')
const now = ref(Date.now() / 1000)
let timer = 0

const left = computed(() => {
  const p = status.value?.pairing
  return p ? Math.max(0, Math.round(p.expires - now.value)) : 0
})

/** 服务那边回来的状态：放进页面；出错就显示原因 */
function apply(r: AiResult<PhoneStatus>): void {
  if (r.ok && r.data) {
    status.value = r.data
    error.value = r.data.error
  } else error.value = r.error ?? t('出错了。')
}

async function load(): Promise<void> {
  if (bridge) apply(await bridge.phoneStatus())
}

async function toggle(on: boolean): Promise<void> {
  if (!bridge) return
  busy.value = true
  apply(await bridge.phoneEnable(on))
  busy.value = false
}

async function pair(): Promise<void> {
  if (!bridge) return
  const r = await bridge.phonePair()
  if (r.ok) await load()
  else error.value = r.error ?? t('出错了。')
}

async function forget(id: string): Promise<void> {
  if (bridge) apply(await bridge.phoneForget(id))
}

function when(ts: number): string {
  return ts ? new Date(ts * 1000).toLocaleString() : t('还没连过')
}

onMounted(() => {
  void load()
  // 配对码倒计时；手机配上以后列表跟着更新
  timer = window.setInterval(() => {
    now.value = Date.now() / 1000
    if (status.value?.pairing || status.value?.enabled) void load()
  }, 2000)
})
onBeforeUnmount(() => clearInterval(timer))
</script>

<template>
  <section class="phone-settings">
    <h2>{{ t('手机') }}</h2>
    <p class="lead">{{ t('手机和电脑连同一个 Wi-Fi 时，用手机浏览器和她聊天、看任务、开任务。不用装 App。') }}</p>

    <div class="card">
      <label class="toggle">
        <input type="checkbox" :checked="status?.enabled" :disabled="busy || !status" @change="toggle(($event.target as HTMLInputElement).checked)" />
        <span>{{ t('允许手机连接') }}</span>
      </label>
      <p class="hint">{{ t('打开后在局域网上开一个端口（{port}）。局域网里是 http：家里的网络用，公共 Wi-Fi 别开。第一次打开时 Windows 可能问要不要允许它通过防火墙，选「专用网络」。', { port: status?.port ?? 8780 }) }}</p>
      <p class="hint">{{ t('手机上能聊天、看任务、开任务（只能在登记过的项目里，固定「先问再改」）。她要改文件、跑命令时，还是要你在电脑上点同意。') }}</p>
      <p v-if="error" class="msg error" role="alert">{{ error }}</p>

      <template v-if="status?.running">
        <h3>{{ t('手机浏览器里打开') }}</h3>
        <ul class="addresses">
          <li v-for="a in status.addresses" :key="a"><code>{{ a }}</code></li>
          <li v-if="!status.addresses.length" class="hint">{{ t('没找到这台电脑在局域网里的地址：确认连着 Wi-Fi 或网线。') }}</li>
        </ul>
        <div class="pairing">
          <button class="btn primary" @click="pair">{{ t('配对一台手机') }}</button>
          <template v-if="status.pairing && left > 0">
            <span class="code" :aria-label="t('配对码')">{{ status.pairing.code }}</span>
            <span class="hint">{{ t('{n} 秒内在手机上输入', { n: left }) }}</span>
          </template>
        </div>
      </template>
    </div>

    <div class="card">
      <h3>{{ t('配过的手机') }}</h3>
      <p v-if="!status?.devices.length" class="hint">{{ t('还没有。') }}</p>
      <ul v-else class="devices">
        <li v-for="d in status.devices" :key="d.id">
          <span class="name">{{ d.name }}</span>
          <span class="hint">{{ t('最近一次连：{when}', { when: when(d.lastSeen) }) }}</span>
          <button class="btn danger" @click="forget(d.id)">{{ t('删掉') }}</button>
        </li>
      </ul>
      <p class="hint">{{ t('删掉以后那台手机马上连不上，要重新配对。') }}</p>
    </div>
  </section>
</template>

<style scoped>
.toggle {
  display: flex;
  align-items: center;
  gap: 10px;
  font-weight: 600;
}

.addresses {
  margin: 6px 0 12px;
  padding: 0;
  list-style: none;
}

.addresses code {
  font-size: 15px;
  user-select: all;
}

.pairing {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
}

.code {
  font: 600 28px/1 var(--ui-mono, Consolas, monospace);
  letter-spacing: 8px;
  color: var(--ui-accent-hi, #c4b5fd);
}

.devices {
  margin: 0;
  padding: 0;
  list-style: none;
}

.devices li {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 6px 0;
}

.devices .name {
  font-weight: 600;
}

.devices .btn {
  margin-left: auto;
}
</style>
