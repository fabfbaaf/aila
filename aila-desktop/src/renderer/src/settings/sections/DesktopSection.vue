<script setup lang="ts">
/** 设置 → 桌面：她什么时候躲开、免打扰、省电、开机启动，以及记下的提醒 */
import { t, dateText, lang } from '../../i18n'
import { computed, onActivated, onBeforeUnmount, onMounted, ref } from 'vue'
import { REPORT_TIMES, SIT_BREAK_CHOICES, type DesktopPrefs, type DesktopStatus } from '../../../../shared/desktop'
import { WORKS_BUDGET_CHOICES, WORKS_EVERY_CHOICES } from '../../../../shared/studio'
import { bridge } from '../../platform/bridge'
import Toggle from '../Toggle.vue'
import { LANG_PREFS, type LangPref } from '../../../../shared/i18n'

const api = bridge!
const status = ref<DesktopStatus | null>(null)

// 界面语言：换了马上生效（所有窗口、托盘菜单），她说话也跟着换
const langPref = ref<LangPref>('auto')
void api.languageGet().then((r) => (langPref.value = r.pref))
async function setLang(v: string): Promise<void> {
  const r = await api.languageSet(v as LangPref)
  if (r) langPref.value = r.pref
}
let off: (() => void) | null = null

const REASONS: Record<string, string> = {
  normal: '在桌面上',
  fullscreen: '藏起来了：别的程序在全屏',
  presentation: '藏起来了：在演示',
  away: '在桌面上（屏幕锁着，她不说话）',
  dnd: '免打扰中：藏起来了',
  off: '不在桌面上显示（只用主窗口）'
}

const now = computed(() => {
  const s = status.value
  if (!s) return ''
  const p = s.presence
  if (p.reason === 'fullscreen' || p.reason === 'presentation') {
    return p.visible ? t('在桌面上，但不说话：别的程序在{what}', { what: p.reason === 'fullscreen' ? t('全屏') : t('演示') }) : t(REASONS[p.reason] ?? '')
  }
  return t(REASONS[p.reason] ?? '')
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

const reported = ref('')

async function reportNow(): Promise<void> {
  reported.value = (await api.desktopReportNow()) ? t('她在桌面上说了。') : t('现在说不了：AI 服务没开，或者她藏起来了。')
}

async function remove(id: string): Promise<void> {
  await api.reminderDelete(id)
}

function when(iso: string): string {
  const d = new Date(iso)
  const today = new Date()
  const tomorrow = new Date(today.getTime() + 24 * 3600 * 1000)
  const time = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  if (d.toDateString() === today.toDateString()) return t('今天 {time}', { time })
  if (d.toDateString() === tomorrow.toDateString()) return t('明天 {time}', { time })
  return dateText(d, true)
}

onMounted(() => {
  off = api.onDesktopStatus((s) => (status.value = s))
})
onBeforeUnmount(() => off?.())
onActivated(load)
</script>

<template>
  <h2>{{ t('桌面') }}</h2>
  <p class="lead">{{ t('她在桌面上什么时候躲开、怎么省电，以及她记下的提醒。') }}</p>

  <template v-if="status">
    <div class="card">
      <h3>{{ lang === 'en' ? 'Language' : t('语言') + ' / Language' }}</h3>
      <div class="row">
        <span class="label">{{ t('界面语言') }}</span>
        <div>
          <select :value="langPref" aria-label="Language" @change="setLang(($event.target as HTMLSelectElement).value)">
            <option v-for="l in LANG_PREFS" :key="l.id" :value="l.id">{{ l.name }}</option>
          </select>
          <span class="hint">{{ t('换了马上生效，她说话也跟着换。现在翻译了主窗口、设置、对话和菜单；任务页、地图、作品页还在翻。') }}</span>
        </div>
      </div>
    </div>
    <div class="card">
      <h3>{{ t('不挡事') }} <span class="state">{{ now }}</span></h3>
      <div class="row">
        <span class="label">{{ t('桌面上显示她') }}</span>
        <div>
          <Toggle :model-value="status.prefs.showCharacter" @update:model-value="(v) => set({ showCharacter: v })" />
          <span class="hint">
            {{ t('关掉以后她不在桌面上常驻：编程任务、对话记录、设置都在主窗口里照常用，到点的提醒改成系统通知。托盘菜单里也能随时打开、关掉。') }}
          </span>
        </div>
      </div>
      <div class="row">
        <span class="label">{{ t('全屏时躲开') }}</span>
        <div>
          <Toggle :model-value="status.prefs.hideOnFullscreen" @update:model-value="(v) => set({ hideOnFullscreen: v })" />
          <span class="hint">{{ t('别的程序全屏（游戏、视频、F11 的浏览器）或者在放幻灯片时，她先藏起来，退出全屏再回来。关掉的话她留着，但不主动说话。') }}</span>
        </div>
      </div>
      <div class="row">
        <span class="label">{{ t('没事时走走') }}</span>
        <div>
          <Toggle :model-value="status.prefs.wander" @update:model-value="(v) => set({ wander: v })" />
          <span class="hint">{{ t('隔一阵自己在屏幕上挪几步，偶尔伸个懒腰。你在打字、全屏、和她说话、离开电脑的时候不动。也可以直接叫她「去右上角」「往左一点」。') }}</span>
        </div>
      </div>
      <div class="row">
        <span class="label">{{ t('出场动画') }}</span>
        <div>
          <Toggle :model-value="status.prefs.entrance" @update:model-value="(v) => set({ entrance: v })" />
          <span class="hint">{{ t('打开艾拉时，屏幕中间先出来一张「艾拉」卡片，她从里面跳到右下角和你打招呼。下次打开生效。') }}</span>
        </div>
      </div>
      <div class="row">
        <span class="label">{{ t('免打扰') }}</span>
        <div>
          <Toggle :model-value="status.dnd" @update:model-value="setDnd" />
          <span class="hint">
            {{ t('开会共享屏幕时用：她藏起来，叫她也不应声，到点的提醒改成系统通知。快捷键') }} <kbd>Alt+Shift+H</kbd>{{ t('，托盘菜单里也有；按') }} <kbd>Alt+Shift+A</kbd> {{ t('和她说话时会自动退出免打扰。') }}
          </span>
        </div>
      </div>
    </div>

    <div class="card">
      <h3>{{ t('省电') }}</h3>
      <div class="row">
        <span class="label">{{ t('没人理她时降帧') }}</span>
        <div>
          <Toggle :model-value="status.prefs.powerSaver" @update:model-value="(v) => set({ powerSaver: v })" />
          <span class="hint">
            {{ t('只在呼吸、眨眼时每秒画 20 帧，鼠标在动时 30 帧，和她说话、点她时不限。笔记本拔了电再往下降。现在{power}。', { power: status.onBattery ? t('用的是电池') : t('接着电源') }) }}
          </span>
        </div>
      </div>
      <div class="row">
        <span class="label">{{ t('开机时启动') }}</span>
        <div>
          <Toggle
            :model-value="status.prefs.openAtLogin"
            :disabled="!status.loginSupported"
            @update:model-value="(v) => set({ openAtLogin: v })"
          />
          <span class="hint">{{ status.loginSupported ? t('登录 Windows 后她自己出来。') : t('从源码跑（npm run dev）时设不了，装了安装包以后才能用。') }}</span>
        </div>
      </div>
    </div>

    <div class="card">
      <h3>{{ t('每天') }}</h3>
      <div class="row">
        <span class="label">{{ t('见面时说说') }}</span>
        <div>
          <span class="hint" style="margin-top: 0">
            {{ t('每天第一次见面（早上开机、解锁回来）她说说昨天一起做了哪些任务、哪个分支还没合并、今天有什么日程和提醒。人不在电脑前、全屏或免打扰时等你回来再说。') }}
          </span>
        </div>
      </div>
      <div class="row">
        <span class="label">{{ t('晚上的日报') }}</span>
        <div>
          <select :value="status.prefs.reportAt" style="width: 160px" @change="(e) => set({ reportAt: (e.target as HTMLSelectElement).value })">
            <option v-for="at in REPORT_TIMES" :key="at" :value="at">{{ at ? t('{time} 说', { time: at }) : t('不说') }}</option>
          </select>
          <button class="btn" style="margin-left: 8px" @click="reportNow">{{ t('现在说说今天') }}</button>
          <span class="hint">{{ t('今天一起做了几个任务、几个还在分支上、用了多少 token、命中多少缓存、聊了几句。右键她「今天做了什么」也行。') }}{{ reported }}</span>
        </div>
      </div>
      <div class="row">
        <span class="label">{{ t('写日记') }}</span>
        <div>
          <Toggle :model-value="status.prefs.diary" @update:model-value="(v) => set({ diary: v })" />
          <span class="hint">{{ t('每天晚上十点以后她写一篇日记：今天一起做了什么、她自己做了什么。只写真的发生的事，在「作品」页的「日记」里看。') }}</span>
        </div>
      </div>
      <div class="row">
        <span class="label">{{ t('每周回顾') }}</span>
        <div>
          <Toggle :model-value="status.prefs.weeklyReview" @update:model-value="(v) => set({ weeklyReview: v })" />
          <span class="hint">{{ t('每周五晚上六点以后写一份这周的回顾：做了什么、还没做完、下周约好的事，改两句就能当周报。材料是这周的编程任务、聊天摘要和提醒，只写真的发生的事。一周一次大模型请求。在「作品」页的「回顾」里看。') }}</span>
        </div>
      </div>
    </div>

    <div class="card">
      <h3>{{ t('她的作品') }}</h3>
      <div class="row">
        <span class="label">{{ t('自己做点东西') }}</span>
        <div>
          <select :value="status.prefs.worksEvery" style="width: 160px" @change="(e) => set({ worksEvery: Number((e.target as HTMLSelectElement).value) })">
            <option v-for="d in WORKS_EVERY_CHOICES" :key="d" :value="d">{{ d === 0 ? t('不让她做') : d === 1 ? t('大约每天一件') : d === 7 ? t('大约每周一件') : t('大约每 {d} 天一件', { d }) }}</option>
          </select>
          <span class="hint">
            {{ t('她会在每个周期里随机挑个时候，按她对你的了解想个小点子（小游戏、小工具、小动画），自己写出来，做好了在桌面上告诉你。只在她自己的文件夹里做，你的项目一个字都不碰；删除、安装这类命令照样停下来等你点头。认识第 100 天这样的日子她也会做一件。') }}
          </span>
        </div>
      </div>
      <div class="row">
        <span class="label">{{ t('每件最多花') }}</span>
        <div>
          <select :value="status.prefs.worksBudget" style="width: 160px" @change="(e) => set({ worksBudget: Number((e.target as HTMLSelectElement).value) })">
            <option v-for="b in WORKS_BUDGET_CHOICES" :key="b" :value="b">{{ t('{b} 元', { b }) }}</option>
          </select>
          <span class="hint">{{ t('到上限就停下（模型的价格要在设置 → 对话里知道，不知道价格时只能靠步数上限）。') }}</span>
        </div>
      </div>
    </div>

    <div class="card">
      <h3>{{ t('提醒') }} <span class="state">{{ status.reminders.length ? t('{n} 条', { n: status.reminders.length }) : '' }}</span></h3>
      <p v-if="!status.reminders.length" class="hint">
        {{ t('还没有。对她说「25 分钟后叫我喝水」「明天上午十点提醒我交周报」，就会记在这里；到点她在桌面上叫你，她藏起来时改用系统通知。') }}
      </p>
      <table v-else class="list">
        <tr v-for="r in status.reminders" :key="r.id">
          <td class="meta">{{ when(r.at) }}</td>
          <td>{{ r.text }}</td>
          <td class="del"><button class="x" :title="t('删掉这个提醒')" @click="remove(r.id)">×</button></td>
        </tr>
      </table>
      <div class="row">
        <span class="label">{{ t('久坐提醒') }}</span>
        <div>
          <select :value="status.prefs.sitBreakMinutes" style="width: 160px" @change="(e) => set({ sitBreakMinutes: Number((e.target as HTMLSelectElement).value) })">
            <option v-for="m in SIT_BREAK_CHOICES" :key="m" :value="m">{{ m ? t('连续用 {m} 分钟', { m }) : t('不提醒') }}</option>
          </select>
          <span class="hint">{{ t('离开电脑 5 分钟以上就重新算。全屏、演示、免打扰时不提醒。') }}</span>
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
