<script setup lang="ts">
/**
 * 开场演出在主窗口里的画面（剧本在 shared/intro.ts，导演在 main/intro.ts，这里只照着 intro:scene 画）：
 *
 * - 导览：切到这一站的页面，聚光灯照着要讲的那个元素（其余暗下去），上面一个小标题；
 *   量出来的位置记在 window.__introSpot，导演读了让她走到旁边去
 * - 停住、故障：她不想讲了，整个应用错位抖动、闪一下反色、扫描线、标题乱码
 * - 玻璃、波纹、法阵、裂纹、碎片不在这里：都在她前面的全屏特效层（FxLayer.vue），她在玻璃后面
 * - 点击波纹：她借你鼠标「点」的地方冒一圈（真的点击没有发生，只是画出来）
 * - 打字：交给对话页的输入框（intents.chatDraftRequest），只填不发
 * - 收尾清单：开始用之前要做的几件事，你自己关
 * - 音效都从这里放（introSfx.ts，右上角能静音）：特效层是个只画画的透明窗口
 *
 * 任何时候点「跳过介绍」或按 Esc 都告诉导演结束。
 */
import { t } from '../i18n'
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { INTRO_TOUR, type IntroScene } from '../../../shared/intro'
import { bridge } from '../platform/bridge'
import BrandMark from '../theme/BrandMark.vue'
import { IntroSfx } from './introSfx'
import { chatDraftRequest, newTaskRequest } from './intents'
import type { ShellRoute } from '../../../shared/shell'

const emit = defineEmits<{ go: [route: ShellRoute] }>()

/** 现在画哪一段：null = 什么都不画（演完、被跳过、还没开始） */
const mode = ref<null | 'tour' | 'stall' | 'glitch' | 'checklist'>(null)
const index = ref(0)
/** 聚光灯照着的地方（窗口里的像素，含一点留边） */
const spot = ref<{ x: number; y: number; w: number; h: number } | null>(null)
/** 故障时的乱码标题（null = 正常标题） */
const garbled = ref<string | null>(null)
const ripples = ref<{ id: number; x: number; y: number }[]>([])
let rippleId = 0
let glitchTimer = 0
let spotToken = 0
const offs: (() => void)[] = []
const sfx = new IntroSfx()
const muted = ref(sfx.muted)

const stop = computed(() => INTRO_TOUR[Math.min(index.value, INTRO_TOUR.length - 1)])
const touring = computed(() => mode.value === 'tour' || mode.value === 'stall' || mode.value === 'glitch')

type SpotWindow = { __introSpot?: { x: number; y: number; width: number; height: number } | null }

function toggleMute(): void {
  sfx.setMuted(!muted.value)
  muted.value = sfx.muted
}

const NOISE = '▓▒░█#@%&*<>/\\|01'
function garble(text: string, amount: number): string {
  return [...text].map((c) => (c !== ' ' && Math.random() < amount ? NOISE[Math.floor(Math.random() * NOISE.length)] : c)).join('')
}

/**
 * 找这一站要照的元素（选择器匹配到的第一个看得见的）；页面刚切过去可能还没画好，多等几回。
 * 找到了记进 window.__introSpot（导演读）；实在找不到记 null，导演就让她站原地讲
 */
async function findSpot(selector: string, token: number): Promise<void> {
  const w = window as unknown as SpotWindow
  for (let i = 0; i < 15; i++) {
    await nextTick()
    await new Promise((r) => setTimeout(r, 80))
    if (token !== spotToken) return
    const el = [...document.querySelectorAll<HTMLElement>(selector)].find((e) => e.getBoundingClientRect().width > 0)
    if (!el) continue
    // 只算露在窗口里的那部分（铺满页面的星图比窗口还大），聚光灯的框也不出窗口
    const b = el.getBoundingClientRect()
    const pad = 8
    const left = Math.max(pad, b.left)
    const top = Math.max(pad, b.top)
    const right = Math.min(innerWidth - pad, b.right)
    const bottom = Math.min(innerHeight - pad, b.bottom)
    const r = { left, top, width: Math.max(0, right - left), height: Math.max(0, bottom - top) }
    spot.value = {
      x: Math.max(2, r.left - pad),
      y: Math.max(2, r.top - pad),
      w: Math.min(innerWidth - 4, r.width + pad * 2),
      h: Math.min(innerHeight - 4, r.height + pad * 2)
    }
    w.__introSpot = { x: Math.round(r.left), y: Math.round(r.top), width: Math.round(r.width), height: Math.round(r.height) }
    return
  }
  spot.value = null
  w.__introSpot = null
}

function setGlitch(on: boolean): void {
  document.body.classList.toggle('intro-glitch', on)
  if (!on) {
    clearInterval(glitchTimer)
    garbled.value = null
  }
}

function onScene(s: IntroScene): void {
  switch (s.scene) {
    case 'tour': {
      if (mode.value !== 'tour' || index.value !== s.index) sfx.play('page')
      mode.value = 'tour'
      index.value = s.index
      setGlitch(false)
      emit('go', INTRO_TOUR[s.index].route)
      ;(window as unknown as SpotWindow).__introSpot = undefined
      void findSpot(INTRO_TOUR[s.index].selector, ++spotToken)
      break
    }
    case 'stall':
      setGlitch(false)
      mode.value = 'stall'
      break
    case 'glitch': {
      mode.value = 'glitch'
      setGlitch(true)
      sfx.play('glitch')
      const title = t(stop.value.title)
      const begin = Date.now()
      clearInterval(glitchTimer)
      glitchTimer = window.setInterval(() => {
        const k = Math.min(1, (Date.now() - begin) / 1600)
        garbled.value = garble(title, 0.15 + k * 0.6)
      }, 70)
      break
    }
    case 'shatter':
      sfx.play('shatter')
      setGlitch(false)
      mode.value = null
      spot.value = null
      break
    case 'click': {
      sfx.play('click')
      const id = ++rippleId
      ripples.value.push({ id, x: s.x, y: s.y })
      window.setTimeout(() => (ripples.value = ripples.value.filter((r) => r.id !== id)), 700)
      break
    }
    case 'type':
      sfx.play('type')
      chatDraftRequest.value = s.text
      break
    case 'sfx':
      sfx.play(s.name)
      break
    case 'checklist':
      sfx.play('chime')
      mode.value = 'checklist'
      break
    case 'end':
      setGlitch(false)
      mode.value = null
      spot.value = null
      break
  }
}

function skip(): void {
  bridge?.introSkip()
}

/** 收尾清单里点一项：去那一页，清单关掉 */
function pick(route: ShellRoute, newTask = false): void {
  mode.value = null
  emit('go', route)
  if (newTask) newTaskRequest.value++
}

function onKey(e: KeyboardEvent): void {
  if (e.key !== 'Escape' || !mode.value) return
  e.preventDefault()
  e.stopPropagation()
  if (mode.value === 'checklist') mode.value = null
  else skip()
}

onMounted(() => {
  if (bridge?.onIntroScene) offs.push(bridge.onIntroScene(onScene))
  window.addEventListener('keydown', onKey, true)
})

onBeforeUnmount(() => {
  setGlitch(false)
  window.removeEventListener('keydown', onKey, true)
  for (const off of offs.splice(0)) off()
})
</script>

<template>
  <!-- 导览：聚光灯 + 小标题；故障时的扫描线；右上角的静音、跳过 -->
  <div v-if="touring" class="intro" :class="mode">
    <div
      v-if="spot"
      :key="index"
      class="spot"
      :style="{ left: `${spot.x}px`, top: `${spot.y}px`, width: `${spot.w}px`, height: `${spot.h}px` }"
    >
      <span class="spot-title" :class="{ below: spot.y < 60 }">{{ garbled ?? t(stop.title) }}</span>
    </div>
    <div v-else class="dim-all"></div>
    <div v-if="mode === 'glitch'" class="scan" aria-hidden="true"></div>
    <div class="ctrl">
      <button class="ghost" :title="muted ? t('打开声音') : t('静音')" @click="toggleMute">{{ muted ? '🔇' : '🔊' }}</button>
      <button class="ghost" @click="skip">{{ t('跳过介绍') }}</button>
    </div>
  </div>

  <!-- 她「点」的地方冒一圈（真的点击没有发生） -->
  <div v-for="r in ripples" :key="r.id" class="ripple" :style="{ left: `${r.x}px`, top: `${r.y}px` }" aria-hidden="true"></div>

  <!-- 收尾：开始用之前的几件事 -->
  <div v-if="mode === 'checklist'" class="checklist" role="dialog" :aria-label="t('开始用之前')">
    <div class="card">
      <BrandMark :size="44" />
      <h2>{{ t('开始用之前') }}</h2>
      <p class="dim">{{ t('这三件做完，她就能真的帮你干活了。以后在设置 → 桌面里能再看一遍开场。') }}</p>
      <button class="item" @click="pick({ page: 'settings', section: 'model' })">
        <b>1</b><span><strong>{{ t('填大模型密钥') }}</strong>{{ t('没有它她只会几句固定的话') }}</span>
      </button>
      <button class="item" @click="pick({ page: 'projects', tab: 'list' })">
        <b>2</b><span><strong>{{ t('登记一个项目') }}</strong>{{ t('她就能按意思搜你的代码、按报错找到出错的地方') }}</span>
      </button>
      <button class="item" @click="pick({ page: 'tasks' }, true)">
        <b>3</b><span><strong>{{ t('开第一个任务') }}</strong>{{ t('选好项目和权限，交给她做') }}</span>
      </button>
      <button class="ghost close" @click="mode = null">{{ t('知道了') }}</button>
    </div>
  </div>
</template>

<style>
/* 故障：整个应用错位抖动、闪两下反色（她不想讲了）；挂在 body 上，因为要动的是整个外壳 */
body.intro-glitch .shell {
  animation: intro-glitch-shift 0.18s steps(2) infinite, intro-glitch-flash 2.2s linear both;
}

@keyframes intro-glitch-shift {
  0% {
    transform: translate(0, 0);
    clip-path: inset(0 0 0 0);
  }
  50% {
    transform: translate(-7px, 1px);
    clip-path: inset(10% 0 28% 0);
  }
  100% {
    transform: translate(6px, -1px);
    clip-path: inset(0 0 0 0);
  }
}

@keyframes intro-glitch-flash {
  0%,
  38%,
  42%,
  70%,
  73%,
  100% {
    filter: none;
  }
  40%,
  71% {
    filter: invert(1) hue-rotate(180deg);
  }
}
</style>

<style scoped>
.intro {
  position: fixed;
  inset: 0;
  z-index: 100;
  pointer-events: none;
}

/* 聚光灯：照着的那一块亮，四周压暗 */
.spot {
  position: fixed;
  border-radius: 12px;
  border: 2px solid rgba(103, 232, 249, 0.9);
  box-shadow:
    0 0 0 9999px rgba(4, 6, 14, 0.55),
    0 0 26px rgba(103, 232, 249, 0.55),
    inset 0 0 18px rgba(103, 232, 249, 0.25);
  animation: spot-in 0.45s ease-out both;
  transition: box-shadow 0.6s ease, border-color 0.6s ease;
}

.stall .spot,
.glitch .spot {
  border-color: rgba(167, 139, 250, 0.6);
  box-shadow:
    0 0 0 9999px rgba(4, 6, 14, 0.68),
    0 0 14px rgba(167, 139, 250, 0.4);
}

.dim-all {
  position: fixed;
  inset: 0;
  background: rgba(4, 6, 14, 0.45);
}

.spot-title {
  position: absolute;
  left: 0;
  bottom: calc(100% + 10px);
  padding: 4px 12px;
  border-radius: 999px;
  background: rgba(10, 14, 26, 0.92);
  border: 1px solid rgba(103, 232, 249, 0.5);
  color: #cffafe;
  font-size: 14px;
  font-weight: 600;
  white-space: nowrap;
  box-shadow: 0 0 14px rgba(103, 232, 249, 0.35);
}

.spot-title.below {
  bottom: auto;
  top: calc(100% + 10px);
}

.glitch .spot-title {
  font-family: 'Cascadia Code', Consolas, monospace;
  text-shadow:
    2px 0 rgba(255, 40, 90, 0.8),
    -2px 0 rgba(0, 220, 255, 0.8);
}

.scan {
  position: fixed;
  inset: 0;
  background: repeating-linear-gradient(0deg, rgba(10, 12, 20, 0.16) 0 2px, transparent 2px 4px);
  animation: scan-move 0.6s linear infinite;
}

.ctrl {
  position: fixed;
  top: 12px;
  right: 16px;
  display: flex;
  gap: 8px;
  pointer-events: auto;
}

.ctrl button {
  padding: 6px 14px;
  border-radius: 8px;
  background: rgba(10, 14, 26, 0.85);
  font-size: 13px;
}

.ripple {
  position: fixed;
  z-index: 120;
  width: 14px;
  height: 14px;
  margin: -7px 0 0 -7px;
  border: 2px solid var(--ui-cyan, #67e8f9);
  border-radius: 50%;
  pointer-events: none;
  animation: ripple 0.65s ease-out both;
}

/* 收尾清单：深色，和艾拉本身一样 */
.checklist {
  position: fixed;
  inset: 0;
  z-index: 110;
  display: grid;
  place-items: center;
  background: rgba(5, 6, 10, 0.45);
  animation: fade 0.3s ease-out both;
}

.card {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 10px;
  width: min(460px, 90vw);
  padding: 24px 26px;
  border: 1px solid var(--ui-line-strong);
  border-radius: var(--ui-r-l);
  background: var(--ui-raised);
  box-shadow: var(--ui-shadow);
}

.card h2 {
  margin: 4px 0 0;
  font-size: 18px;
}

.card .dim {
  margin: 0 0 6px;
  color: var(--ui-muted);
  font-size: 13px;
  line-height: 1.6;
}

.item {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  width: 100%;
  padding: 10px 12px;
  border: 1px solid var(--ui-line);
  border-radius: var(--ui-r);
  background: var(--ui-surface);
  color: var(--ui-text-2);
  font: inherit;
  font-size: 13px;
  text-align: left;
  cursor: pointer;
}

.item:hover {
  border-color: var(--ui-accent-line);
  background: var(--ui-accent-soft);
}

.item b {
  flex: none;
  display: grid;
  place-items: center;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: var(--ui-accent-soft);
  color: var(--ui-accent-hi);
  font-size: 12px;
}

.item span {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.item strong {
  color: var(--ui-text);
  font-size: 14px;
}

.close {
  align-self: flex-end;
  margin-top: 4px;
}

@keyframes spot-in {
  from {
    opacity: 0;
    transform: scale(1.08);
  }
  to {
    opacity: 1;
    transform: none;
  }
}

@keyframes scan-move {
  from {
    background-position: 0 0;
  }
  to {
    background-position: 0 8px;
  }
}

@keyframes ripple {
  from {
    opacity: 1;
    transform: scale(0.6);
  }
  to {
    opacity: 0;
    transform: scale(3.2);
  }
}

@keyframes fade {
  from {
    opacity: 0;
  }
  to {
    opacity: 1;
  }
}
</style>
