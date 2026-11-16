<script setup lang="ts">
/** 设置 → 桌面：她什么时候躲开、免打扰、省电、开机启动，以及记下的提醒 */
import { computed, onActivated, onBeforeUnmount, onMounted, ref } from 'vue'
import { SIT_BREAK_CHOICES, type DesktopPrefs, type DesktopStatus } from '../../../../shared/desktop'
import { bridge } from '../../platform/bridge'
import Toggle from '../Toggle.vue'

const api = bridge!
const status = ref<DesktopStatus | null>(null)
let off: (() => void) | null = null

const REASONS: Record<string, string> = {
  normal: '在桌面上',
  fullscreen: '藏起来了：别的程序在全屏',
  presentation: '藏起来了：在演示',
  away: '在桌面上（屏幕锁着，她不说话）',
  dnd: '免打扰中：藏起来了'
}

const now = computed(() => {
  const s = status.value
  if (!s) return ''
  const p = s.presence
  if (p.reason === 'fullscreen' || p.reason === 'presentation') {
    return p.visible ? `在桌面上，但不说话：别的程序在${p.reason === 'fullscreen' ? '全屏' : '演示'}` : REASONS[p.reason]
  }
  return REASONS[p.reason] ?? ''
})

async function load(): Promise<void> {
  status.value = await api.desktopStatus()
}

async function set(patch: Partial<DesktopPrefs>): Promise<void> {
  const next = await api.desktopSetPrefs(patch)
  if (next) status.value = next
}

async function setDnd(on: boolean): Promise<void> {
  await api.desktopSetDnd(on)
}

async function remove(id: string): Promise<void> {
  await api.reminderDelete(id)
}

function when(iso: string): string {
  const d = new Date(iso)
  const today = new Date()
  const tomorrow = new Date(today.getTime() + 24 * 3600 * 1000)
  const time = d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
  if (d.toDateString() === today.toDateString()) return `今天 ${time}`
  if (d.toDateString() === tomorrow.toDateString()) return `明天 ${time}`
  return `${d.getMonth() + 1} 月 ${d.getDate()} 日 ${time}`
}

onMounted(() => {
  off = api.onDesktopStatus((s) => (status.value = s))
})
onBeforeUnmount(() => off?.())
onActivated(load)
</script>

<template>
  <h2>桌面</h2>
  <p class="lead">她在桌面上什么时候躲开、怎么省电，以及她记下的提醒。</p>

  <template v-if="status">
    <div class="card">
      <h3>不挡事 <span class="state">{{ now }}</span></h3>
      <div class="row">
        <span class="label">全屏时躲开</span>
        <div>
          <Toggle :model-value="status.prefs.hideOnFullscreen" @update:model-value="(v) => set({ hideOnFullscreen: v })" />
          <span class="hint">别的程序全屏（游戏、视频、F11 的浏览器）或者在放幻灯片时，她先藏起来，退出全屏再回来。关掉的话她留着，但不主动说话。</span>
        </div>
      </div>
      <div class="row">
        <span class="label">免打扰</span>
        <div>
          <Toggle :model-value="status.dnd" @update:model-value="setDnd" />
          <span class="hint">
            开会共享屏幕时用：她藏起来，叫她也不应声，到点的提醒改成系统通知。快捷键 <kbd>Alt+Shift+H</kbd>，托盘菜单里也有；
            按 <kbd>Alt+Shift+A</kbd> 和她说话时会自动退出免打扰。
          </span>
        </div>
      </div>
    </div>

    <div class="card">
      <h3>省电</h3>
      <div class="row">
        <span class="label">没人理她时降帧</span>
        <div>
          <Toggle :model-value="status.prefs.powerSaver" @update:model-value="(v) => set({ powerSaver: v })" />
          <span class="hint">
            只在呼吸、眨眼时每秒画 20 帧，鼠标在动时 30 帧，和她说话、点她时不限。笔记本拔了电再往下降。现在{{
              status.onBattery ? '用的是电池' : '接着电源'
            }}。
          </span>
        </div>
      </div>
      <div class="row">
        <span class="label">开机时启动</span>
        <div>
          <Toggle
            :model-value="status.prefs.openAtLogin"
            :disabled="!status.loginSupported"
            @update:model-value="(v) => set({ openAtLogin: v })"
          />
          <span class="hint">{{ status.loginSupported ? '登录 Windows 后她自己出来。' : '从源码跑（npm run dev）时设不了，装了安装包以后才能用。' }}</span>
        </div>
      </div>
    </div>

    <div class="card">
      <h3>提醒 <span class="state">{{ status.reminders.length ? `${status.reminders.length} 条` : '' }}</span></h3>
      <p v-if="!status.reminders.length" class="hint">
        还没有。对她说「25 分钟后叫我喝水」「明天上午十点提醒我交周报」，就会记在这里；到点她在桌面上叫你，她藏起来时改用系统通知。
      </p>
      <table v-else class="list">
        <tr v-for="r in status.reminders" :key="r.id">
          <td class="meta">{{ when(r.at) }}</td>
          <td>{{ r.text }}</td>
          <td class="del"><button class="x" title="删掉这个提醒" @click="remove(r.id)">×</button></td>
        </tr>
      </table>
      <div class="row">
        <span class="label">久坐提醒</span>
        <div>
          <select :value="status.prefs.sitBreakMinutes" style="width: 160px" @change="(e) => set({ sitBreakMinutes: Number((e.target as HTMLSelectElement).value) })">
            <option v-for="m in SIT_BREAK_CHOICES" :key="m" :value="m">{{ m ? `连续用 ${m} 分钟` : '不提醒' }}</option>
          </select>
          <span class="hint">离开电脑 5 分钟以上就重新算。全屏、演示、免打扰时不提醒。</span>
        </div>
      </div>
    </div>
  </template>
</template>

<style scoped>
.state {
  margin-left: 6px;
  color: var(--muted);
  font-size: 12px;
  font-weight: 400;
}

.meta {
  width: 1%;
  color: var(--muted);
  white-space: nowrap;
}

.del {
  width: 1%;
}

kbd {
  padding: 0 4px;
  border: 1px solid var(--line);
  border-radius: 3px;
  font-size: 11px;
}
</style>
