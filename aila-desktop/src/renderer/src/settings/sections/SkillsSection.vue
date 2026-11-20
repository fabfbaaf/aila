<script setup lang="ts">
/**
 * 技能包：对话里她能用的工具按技能包分组，单独开关。
 * embedded：嵌在设置的「技能和扩展」页里（不单独成页，标题换小一号）。
 * 外部工具（MCP）的加、删、开关在同一页的 McpCard 里，这里不再重复。
 */
import { computed, onActivated, onMounted, ref } from 'vue'
import type { SkillInfo } from '../../../../shared/cloud'
import type { ScreenStatus } from '../../../../shared/settings'
import { bridge } from '../../platform/bridge'
import { store } from '../store'
import Toggle from '../Toggle.vue'

type SkillRow = SkillInfo

defineProps<{ embedded?: boolean }>()

const api = bridge!
const skills = ref<SkillRow[] | null>(null)
const loaded = ref(false)
const screen = ref<ScreenStatus | null>(null)

async function load(): Promise<void> {
  const [list, status] = await Promise.all([api.skillsList(), api.screenStatus()])
  skills.value = list
  screen.value = status.data ?? null
  loaded.value = true
}

/** 漏斗每层过滤掉多少：看了几次 → 没截图 / 画面没变 → 识别了几次 → 认出几个报错 → 问了你几次 */
const funnel = computed(() => {
  const s = screen.value?.stats
  if (!s || !s.ticks) return ''
  const pct = (n: number): string => `${Math.round((100 * n) / s.ticks)}%`
  return (
    `上次一共看了 ${s.ticks} 次：${s.other_app} 次前台不是写代码的程序（${pct(s.other_app)}，没截图），` +
    `${s.unchanged + s.unsettled} 次画面没变或还在变（${pct(s.unchanged + s.unsettled)}），` +
    `识别文字 ${s.ocr} 次（${pct(s.ocr)}，平均 ${s.ocr_avg_ms} 毫秒），认出报错 ${s.errors} 次，问了你 ${s.asked} 次。`
  )
})

function toggle(s: SkillRow, on: boolean): void {
  api.skillsSet(s.id, on)
  s.enabled = on
}

function unavailable(s: SkillRow): string {
  return s.id === 'knowledge' ? '资料库里还没有文件' : '暂时用不了'
}

onMounted(load)
onActivated(load)
</script>

<template>
  <template v-if="embedded">
    <h3 class="section-title">对话里的技能包</h3>
    <p class="hint section-lead">和她聊天时能用的工具按技能包分组，可以单独开关；扩展带的工具服务也会出现在这里。登录后开关跟着账号同步。</p>
  </template>
  <template v-else>
    <h2>技能包</h2>
    <p class="lead">她能用的工具按技能包分组，可以单独开关。关掉后她就不会再用那一类工具。登录后开关跟着账号同步。</p>
  </template>

  <div v-if="loaded && !skills" class="offline">
    连不上 AI 服务，请先启动 aila-ai。
    <button class="link" @click="load">重试</button>
  </div>

  <div v-for="s in skills ?? []" :key="s.id" class="card skill">
    <div class="top">
      <div>
        <h3>{{ s.name }}</h3>
        <span class="hint">{{ s.description }}{{ s.available ? '' : `（${unavailable(s)}）` }}</span>
      </div>
      <Toggle :model-value="s.enabled" @update:model-value="(v) => toggle(s, v)" />
    </div>
    <div v-if="s.tools?.length" class="tools">
      <span v-for="t in s.tools" :key="t.name" class="tag" :class="{ warn: t.risk !== 'read' }">
        {{ t.name }}{{ t.risk === 'mixed' ? ' · 有时要你确认' : t.risk !== 'read' ? ' · 要你确认' : '' }}
      </span>
    </div>
  </div>

  <div class="card skill">
    <div class="top">
      <div>
        <h3>留意屏幕上的报错</h3>
        <span class="hint">开着时她每 2 秒看一眼前台窗口，看到报错先问你要不要帮忙。每台电脑单独开关，默认关闭。</span>
      </div>
      <Toggle :model-value="store.voice.watch" @update:model-value="(v) => api.voiceSet('watch', v)" />
    </div>
    <p v-if="screen && !screen.available" class="hint">这台电脑上用不了（现在只支持 Windows）。</p>
    <p class="hint">
      只看写代码的程序，别的窗口连截图都不截：
      <span v-for="a in screen?.apps ?? []" :key="a" class="tag">{{ a.replace(/\.exe$/, '') }}</span>
    </p>
    <p class="hint">
      截图和识别出的文字只在内存里，不存硬盘、不上传；同一个错误只问一次；你点「好」才把报错附近的几行（密钥会打码）交给大模型。
      开着时她脚边会显示「正在看屏幕」。
    </p>
    <p v-if="funnel" class="hint">{{ funnel }}</p>
  </div>

</template>

<style scoped>
.skill .top {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
}

.skill h3 {
  margin-bottom: 2px;
}

.tools {
  margin-top: 8px;
}

.section-title {
  margin: 26px 0 4px;
}

.section-lead {
  margin: 0 0 10px;
}

code {
  padding: 0 4px;
  border-radius: 4px;
  background: var(--tag-bg);
  font-size: 11.5px;
}
</style>
