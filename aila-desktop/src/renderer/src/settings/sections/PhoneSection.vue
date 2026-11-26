<script setup lang="ts">
/**
 * 设置 → 手机：同一个 Wi-Fi 下用手机和她聊天、看任务、开任务（AI 服务那边的门：aila-ai/app/phone）。
 * 默认关着；打开后给地址和 6 位配对码，配过的手机在这里看、删。同意她改文件、跑命令还是只能在电脑上点。
 */
import { t } from '../../i18n'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import type { AiResult, AndroidStatus, PhoneStatus, PushKind } from '../../../../shared/settings'
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
    if (!status.value) pushKind.value = r.data.push.kind
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

// ---------- 推到手机通知栏 ----------

const PUSH_KINDS: { id: PushKind; name: string; hint: string }[] = [
  { id: 'none', name: '不推', hint: '' },
  { id: 'ntfy', name: 'ntfy', hint: 'https://ntfy.sh/你自己起的一个难猜的名字（手机上装 ntfy 订阅同一个名字）' },
  { id: 'bark', name: 'Bark（iPhone）', hint: 'https://api.day.app/你的 Bark 密钥' },
  { id: 'wecom', name: '企业微信机器人', hint: 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=…' },
  { id: 'feishu', name: '飞书机器人', hint: 'https://open.feishu.cn/open-apis/bot/v2/hook/…' }
]
const pushKind = ref<PushKind>('none')
const pushUrl = ref('')
const pushMsg = ref('')
const pushHint = computed(() => PUSH_KINDS.find((k) => k.id === pushKind.value)?.hint ?? '')

async function savePush(): Promise<void> {
  if (!bridge) return
  pushMsg.value = ''
  const r = await bridge.phonePush(pushKind.value, pushUrl.value.trim())
  if (r.ok && r.data) {
    status.value = r.data
    pushUrl.value = ''
    pushMsg.value = t('存好了。')
  } else pushMsg.value = r.error ?? t('出错了。')
}

async function testPush(): Promise<void> {
  if (!bridge) return
  const r = await bridge.phonePushTest()
  pushMsg.value = r.ok ? t('推了一条，看看手机上收到没。') : r.error ?? t('出错了。')
}

// ---------- 让她操作安卓手机（ADB） ----------

const android = ref<AndroidStatus | null>(null)
const androidError = ref('')
const androidBusy = ref(false)
const pairAddress = ref('')
const pairCode = ref('')
const connectAddress = ref('')

const ready = computed(() => android.value?.devices.filter((d) => d.state === 'device') ?? [])

function applyAndroid(r: AiResult<AndroidStatus>): void {
  if (r.ok && r.data) {
    android.value = r.data
    androidError.value = r.data.error
  } else androidError.value = r.error ?? t('出错了。')
}

async function loadAndroid(): Promise<void> {
  if (bridge) applyAndroid(await bridge.androidStatus())
}

async function androidDo(fn: () => Promise<AiResult<AndroidStatus>>): Promise<void> {
  androidBusy.value = true
  applyAndroid(await fn())
  androidBusy.value = false
}

function setAndroid(body: { enabled?: boolean; serial?: string }): void {
  if (bridge) void androidDo(() => bridge!.androidSet(body))
}

function pairAndroid(): void {
  if (bridge) void androidDo(() => bridge!.androidPair(pairAddress.value.trim(), pairCode.value.trim()))
}

function connectAndroid(): void {
  if (bridge) void androidDo(() => bridge!.androidConnect(connectAddress.value.trim()))
}

function stateText(state: string): string {
  if (state === 'device') return t('能用')
  if (state === 'unauthorized') return t('在手机上点「允许 USB 调试」')
  return t('断开了')
}

onMounted(() => {
  void load()
  void loadAndroid()
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
      <h3>{{ t('推到手机通知栏') }}</h3>
      <p class="hint">{{ t('她等你同意、任务做完或出错时，手机上的页面开着会弹一条；页面关着时可以借一个推送服务推到通知栏。推出去的只有任务标题和状态，不带命令和代码。') }}</p>
      <div class="push-row">
        <select v-model="pushKind">
          <option v-for="k in PUSH_KINDS" :key="k.id" :value="k.id">{{ t(k.name) }}</option>
        </select>
        <input
          v-if="pushKind !== 'none'"
          v-model="pushUrl"
          type="password"
          autocomplete="off"
          :placeholder="status?.push.set && status.push.kind === pushKind ? t('已设置（不显示）。要换就填新的') : t(pushHint)"
        />
        <button class="btn" @click="savePush">{{ t('保存') }}</button>
        <button v-if="status?.push.set" class="btn" @click="testPush">{{ t('推一条试试') }}</button>
      </div>
      <p v-if="pushMsg" class="hint" role="status">{{ pushMsg }}</p>
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

    <div class="card" :aria-busy="androidBusy">
      <h3>{{ t('让她操作安卓手机') }}</h3>
      <p class="hint">{{ t('她通过 ADB（安卓的调试工具）看你手机的屏幕、点、打字、打开 App。发送、支付、删除、确认这类按钮先问你，支付和银行 App 里点什么都先问；密码、验证码不碰。只支持安卓。') }}</p>
      <template v-if="android && !android.adb">
        <p class="msg error">{{ t('没找到 ADB。装 Android SDK Platform-Tools（谷歌官方，免费），把它放进 PATH 或者装在默认位置，再点刷新。') }}</p>
        <button class="btn" @click="loadAndroid">{{ t('刷新') }}</button>
      </template>
      <template v-else-if="android">
        <label class="toggle">
          <input type="checkbox" :checked="android.enabled" :disabled="androidBusy" @change="setAndroid({ enabled: ($event.target as HTMLInputElement).checked })" />
          <span>{{ t('让她操作安卓手机') }}</span>
        </label>
        <p class="hint">{{ t('手机上先打开「开发者选项 → USB 调试」用线连上；或者打开「无线调试」，在下面配对、连接。') }}</p>
        <ul class="devices">
          <li v-for="d in android.devices" :key="d.serial">
            <label class="pick">
              <input type="radio" name="android" :value="d.serial" :checked="android.serial === d.serial" :disabled="d.state !== 'device' || androidBusy" @change="setAndroid({ serial: d.serial })" />
              <span class="name">{{ d.model || d.serial }}</span>
            </label>
            <span class="hint">{{ stateText(d.state) }}</span>
          </li>
          <li v-if="!android.devices.length" class="hint">{{ t('还没连上手机。') }}</li>
        </ul>
        <p v-if="android.enabled && !android.serial && ready.length" class="hint">{{ t('选一台手机，她才能操作。') }}</p>
        <details class="wireless">
          <summary>{{ t('无线调试（不用线）') }}</summary>
          <p class="hint">{{ t('手机上：设置 → 开发者选项 → 无线调试 → 使用配对码配对设备，把显示的 IP:端口 和配对码填这里。') }}</p>
          <div class="row">
            <input v-model="pairAddress" placeholder="192.168.1.20:37123" />
            <input v-model="pairCode" maxlength="6" inputmode="numeric" :placeholder="t('配对码')" />
            <button class="btn" :disabled="androidBusy" @click="pairAndroid">{{ t('配对') }}</button>
          </div>
          <p class="hint">{{ t('配对成功后，填「无线调试」页面最上面的 IP:端口 连接。') }}</p>
          <div class="row">
            <input v-model="connectAddress" placeholder="192.168.1.20:41055" />
            <button class="btn" :disabled="androidBusy" @click="connectAndroid">{{ t('连接') }}</button>
          </div>
        </details>
        <button class="btn" :disabled="androidBusy" @click="loadAndroid">{{ t('刷新') }}</button>
      </template>
      <p v-if="androidError" class="msg error" role="alert">{{ androidError }}</p>
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

.push-row {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.push-row input {
  flex: 1;
  min-width: 220px;
}

.pick {
  display: flex;
  align-items: center;
  gap: 8px;
}

.wireless {
  margin: 10px 0;
}

.wireless summary {
  cursor: pointer;
}

.wireless .row {
  display: flex;
  gap: 8px;
  margin: 6px 0;
}

.wireless input {
  min-width: 0;
  flex: 1;
}
</style>
