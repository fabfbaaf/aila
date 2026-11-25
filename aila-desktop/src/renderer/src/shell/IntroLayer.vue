<script setup lang="ts">
/**
 * 开场演出在主窗口里的画面（剧本在 shared/intro.ts，导演在 main/intro.ts，这里只照着 intro:scene 画）：
 *
 * - 幻灯片：一份很「正式」的浅色产品介绍，左边留一栏给她站（她是桌面上的透明窗口，正好站在这一栏前面）
 * - 停住、故障：标题乱码、画面错位抖动、扫描线
 * - 裂纹：从她敲的地方往外裂，一下比一下多；碎掉时整块玻璃碎成几片掉下去，露出后面真正的艾拉
 * - 点击波纹：她借你鼠标「点」的地方冒一圈（真的点击没有发生，只是画出来）
 * - 打字：交给对话页的输入框（intents.chatDraftRequest），只填不发
 * - 收尾清单：开始用之前要做的几件事，你自己关
 *
 * - 每页一个小演示（IntroDemo.vue），翻页、打字、故障、敲、碎、点都有音效（introSfx.ts，右上角能静音）
 *
 * 任何时候点「跳过介绍」或按 Esc 都告诉导演结束。
 */
import { t } from '../i18n'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { INTRO_SLIDES, PRESENTER_RATIO, type IntroScene } from '../../../shared/intro'
import { bridge } from '../platform/bridge'
import BrandMark from '../theme/BrandMark.vue'
import IntroDemo from './IntroDemo.vue'
import { IntroSfx } from './introSfx'
import { chatDraftRequest, newTaskRequest } from './intents'
import type { ShellRoute } from '../../../shared/shell'

const emit = defineEmits<{ go: [route: ShellRoute] }>()

/** 现在画哪一段：null = 什么都不画（演完、被跳过、还没开始） */
const mode = ref<null | 'slides' | 'stall' | 'glitch' | 'shatter' | 'checklist'>(null)
const index = ref(0)
const crack = ref<{ x: number; y: number; stage: number } | null>(null)
const shaking = ref(false)
const ripples = ref<{ id: number; x: number; y: number }[]>([])
/** 故障时的乱码标题（null = 正常标题） */
const garbled = ref<string | null>(null)
let rippleId = 0
const sfx = new IntroSfx()
const muted = ref(sfx.muted)
function toggleMute(): void {
  sfx.setMuted(!muted.value)
  muted.value = sfx.muted
}
let glitchTimer = 0
const offs: (() => void)[] = []

const slide = computed(() => INTRO_SLIDES[Math.min(index.value, INTRO_SLIDES.length - 1)])
const glass = computed(() => mode.value === 'slides' || mode.value === 'stall' || mode.value === 'glitch' || mode.value === 'shatter')

const NOISE = '▓▒░█#@%&*<>/\\|01'
function garble(text: string, amount: number): string {
  return [...text].map((c) => (c !== ' ' && Math.random() < amount ? NOISE[Math.floor(Math.random() * NOISE.length)] : c)).join('')
}

/** 裂纹：从敲的点往外的几条折线；stage 越大条数越多、越长。用固定的「随机」让每次画出来一样 */
const cracks = computed(() => {
  const c = crack.value
  if (!c) return []
  const lines: string[] = []
  const count = 3 + c.stage * 3
  for (let i = 0; i < count; i++) {
    const base = (i / count) * Math.PI * 2 + (i % 2 ? 0.35 : -0.2)
    let x = c.x
    let y = c.y
    let d = `M${x} ${y}`
    const segs = 2 + c.stage
    for (let s = 0; s < segs; s++) {
      const wobble = Math.sin(i * 12.9898 + s * 78.233) * 0.45
      const len = (28 + ((i * 37 + s * 53) % 40)) * (0.8 + c.stage * 0.45)
      x += Math.cos(base + wobble) * len
      y += Math.sin(base + wobble) * len
      d += ` L${Math.round(x)} ${Math.round(y)}`
    }
    lines.push(d)
  }
  return lines
})

/** 碎片：以裂纹中心把画面切成几块三角形，各自往下掉、转一点 */
const shards = computed(() => {
  const c = crack.value ?? { x: window.innerWidth * PRESENTER_RATIO, y: window.innerHeight * 0.6 }
  const w = window.innerWidth
  const h = window.innerHeight
  const corners: [number, number][] = [[0, 0], [w * 0.45, 0], [w, 0], [w, h * 0.5], [w, h], [w * 0.5, h], [0, h], [0, h * 0.45]]
  return corners.map((p, i) => {
    const q = corners[(i + 1) % corners.length]
    const toPct = (x: number, y: number): string => `${((x / w) * 100).toFixed(1)}% ${((y / h) * 100).toFixed(1)}%`
    return {
      clip: `polygon(${toPct(c.x, c.y)}, ${toPct(p[0], p[1])}, ${toPct(q[0], q[1])})`,
      dx: (p[0] + q[0]) / 2 > c.x ? 1 : -1,
      delay: (i % 4) * 0.05,
      spin: (i % 2 ? 1 : -1) * (6 + i * 2)
    }
  })
})

function onScene(s: IntroScene): void {
  switch (s.scene) {
    case 'slides':
      if (mode.value !== 'slides' || index.value !== s.index) sfx.play('page')
      mode.value = 'slides'
      index.value = s.index
      crack.value = null
      garbled.value = null
      break
    case 'stall':
      mode.value = 'stall'
      break
    case 'glitch': {
      mode.value = 'glitch'
      sfx.play('glitch')
      const title = t(slide.value.title)
      const begin = Date.now()
      clearInterval(glitchTimer)
      glitchTimer = window.setInterval(() => {
        const k = Math.min(1, (Date.now() - begin) / 1600)
        garbled.value = garble(title, 0.15 + k * 0.6)
      }, 70)
      break
    }
    case 'crack':
      clearInterval(glitchTimer)
      sfx.play('knock')
      window.setTimeout(() => sfx.play('crack'), 60)
      crack.value = { x: s.x, y: s.y, stage: s.stage }
      shaking.value = false
      requestAnimationFrame(() => (shaking.value = true))
      break
    case 'shatter':
      sfx.play('shatter')
      mode.value = 'shatter'
      window.setTimeout(() => mode.value === 'shatter' && (mode.value = null), 1200)
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
      clearInterval(glitchTimer)
      mode.value = null
      crack.value = null
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
  clearInterval(glitchTimer)
  window.removeEventListener('keydown', onKey, true)
  for (const off of offs.splice(0)) off()
})
</script>

<template>
  <div v-if="glass" class="intro" :class="[mode, { shaking }]" @animationend="shaking = false">
    <!-- 一本正经的幻灯片：碎的时候这一层整个淡掉，换成下面的碎片 -->
    <div class="deck">
      <div class="presenter" :style="{ width: `${PRESENTER_RATIO * 100}%` }">
        <span class="role">{{ t('讲解') }}</span>
      </div>
      <section class="slide">
        <header class="head">
          <BrandMark :size="34" />
          <span class="org">{{ t('艾拉 · 产品介绍') }}</span>
          <button class="mute" :title="muted ? t('打开声音') : t('静音')" @click="toggleMute">{{ muted ? '🔇' : '🔊' }}</button>
          <button class="skip" @click="skip">{{ t('跳过介绍') }}</button>
        </header>
        <h1 :key="index">{{ garbled ?? t(slide.title) }}</h1>
        <ul :key="`p${index}`" class="points">
          <li v-for="(p, i) in slide.points" :key="p" :style="{ animationDelay: `${0.25 + i * 0.18}s` }">{{ mode === 'glitch' ? garble(t(p), 0.35) : t(p) }}</li>
        </ul>
        <IntroDemo :key="`d${index}`" :demo="slide.demo" @sfx="(n) => sfx.play(n)" />
        <footer class="foot">
          <span>{{ index + 1 }} / {{ INTRO_SLIDES.length }}</span>
          <span class="bar"><i :style="{ width: `${((index + 1) / INTRO_SLIDES.length) * 100}%` }"></i></span>
        </footer>
      </section>
      <div class="scan" aria-hidden="true"></div>
    </div>
    <svg v-if="crack" class="cracks" aria-hidden="true">
      <path v-for="(d, i) in cracks" :key="i" :d="d" />
      <circle :cx="crack.x" :cy="crack.y" :r="6 + crack.stage * 4" />
    </svg>
    <div v-if="mode === 'shatter'" class="shards" aria-hidden="true">
      <div
        v-for="(s, i) in shards"
        :key="i"
        class="shard"
        :style="{ clipPath: s.clip, '--dx': s.dx, '--spin': `${s.spin}deg`, animationDelay: `${s.delay}s` }"
      ></div>
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

<style scoped>
.intro {
  position: fixed;
  inset: 0;
  z-index: 100;
  overflow: hidden;
}

/* 一本正经：浅色、规矩、像公司里的汇报幻灯片，和后面深色的艾拉完全不一样 */
.deck {
  position: absolute;
  inset: 0;
  display: flex;
  background: #f5f6fa;
  color: #1f2433;
  font-family: 'Microsoft YaHei UI', 'Microsoft YaHei', 'PingFang SC', sans-serif;
}

.presenter {
  position: relative;
  flex: none;
  background: linear-gradient(180deg, #eceef5, #e3e6f0);
  border-right: 1px solid #d9dce6;
}

.role {
  position: absolute;
  left: 18px;
  top: 18px;
  padding: 3px 10px;
  border-radius: 999px;
  background: #dfe3ee;
  color: #6a7186;
  font-size: 12px;
}

.slide {
  flex: 1;
  display: flex;
  flex-direction: column;
  padding: 28px 48px 24px;
  min-width: 0;
}

.head {
  display: flex;
  align-items: center;
  gap: 12px;
}

.org {
  color: #6a7186;
  font-size: 13px;
  letter-spacing: 0.04em;
}

.mute {
  margin-left: auto;
  padding: 5px 9px;
  border: 1px solid #cfd3df;
  border-radius: 6px;
  background: #fff;
  font-size: 13px;
  cursor: pointer;
}

.skip {
  padding: 6px 14px;
  border: 1px solid #cfd3df;
  border-radius: 6px;
  background: #fff;
  color: #4a5063;
  font: inherit;
  font-size: 13px;
  cursor: pointer;
}

.skip:hover {
  border-color: #9aa1b5;
}

h1 {
  margin: 5vh 0 14px;
  font-size: 36px;
  font-weight: 700;
  letter-spacing: 0.02em;
  animation: rise 0.5s ease-out both;
}

ul {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-wrap: wrap;
  gap: 8px 22px;
}

li {
  position: relative;
  padding-left: 16px;
  font-size: 15px;
  color: #3a4155;
  animation: rise 0.45s ease-out both;
}

li::before {
  content: '';
  position: absolute;
  left: 0;
  top: 0.55em;
  width: 7px;
  height: 7px;
  border-radius: 2px;
  background: #4f6bed;
}

.foot {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-top: auto;
  color: #8a90a3;
  font-size: 13px;
}

.foot .bar {
  flex: 1;
  height: 3px;
  border-radius: 2px;
  background: #e1e4ee;
  overflow: hidden;
}

.foot .bar i {
  display: block;
  height: 100%;
  background: #4f6bed;
  transition: width 0.5s ease;
}

.scan {
  position: absolute;
  inset: 0;
  pointer-events: none;
  opacity: 0;
}

/* 停住：幻灯片微微暗下去，像卡住了 */
.stall .deck {
  filter: saturate(0.7) brightness(0.97);
  transition: filter 0.6s;
}

/* 故障：错位、红青分离、扫描线、偶尔一闪反色 */
.glitch .deck {
  animation: glitch-shift 0.18s steps(2) infinite, glitch-flash 1.8s linear both;
}

.glitch h1,
.glitch li {
  text-shadow: 2px 0 rgba(255, 40, 90, 0.75), -2px 0 rgba(0, 220, 255, 0.75);
  font-family: 'Cascadia Code', Consolas, monospace;
}

.glitch .scan {
  opacity: 1;
  background: repeating-linear-gradient(0deg, rgba(10, 12, 20, 0.08) 0 2px, transparent 2px 4px);
  animation: scan-move 0.6s linear infinite;
}

.shaking .deck,
.shaking .cracks {
  animation: knock-shake 0.22s ease-out;
}

.cracks {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
}

.cracks path {
  fill: none;
  stroke: rgba(30, 34, 52, 0.75);
  stroke-width: 1.6;
  stroke-linejoin: round;
  filter: drop-shadow(0 0 1px rgba(255, 255, 255, 0.9));
}

.cracks circle {
  fill: rgba(255, 255, 255, 0.45);
  stroke: rgba(30, 34, 52, 0.6);
  stroke-width: 1.2;
}

/* 碎掉：幻灯片那层淡掉，碎片带着同样的浅色往下掉、转一点 */
.shatter .deck,
.shatter .cracks {
  animation: vanish 0.15s linear both;
}

.shards {
  position: absolute;
  inset: 0;
  pointer-events: none;
}

.shard {
  position: absolute;
  inset: 0;
  background: #f5f6fa;
  box-shadow: inset 0 0 0 1px rgba(30, 34, 52, 0.25);
  animation: fall 1s cubic-bezier(0.55, 0, 0.9, 0.5) both;
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

@keyframes rise {
  from {
    opacity: 0;
    transform: translateY(8px);
  }
  to {
    opacity: 1;
    transform: none;
  }
}

@keyframes glitch-shift {
  0% {
    transform: translate(0, 0);
    clip-path: inset(0 0 0 0);
  }
  50% {
    transform: translate(-6px, 1px);
    clip-path: inset(12% 0 30% 0);
  }
  100% {
    transform: translate(5px, -1px);
    clip-path: inset(0 0 0 0);
  }
}

@keyframes glitch-flash {
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

@keyframes scan-move {
  from {
    background-position: 0 0;
  }
  to {
    background-position: 0 8px;
  }
}

@keyframes knock-shake {
  0% {
    transform: translate(0, 0);
  }
  30% {
    transform: translate(4px, -2px);
  }
  60% {
    transform: translate(-3px, 1px);
  }
  100% {
    transform: none;
  }
}

@keyframes vanish {
  to {
    opacity: 0;
  }
}

@keyframes fall {
  from {
    transform: none;
    opacity: 1;
  }
  to {
    transform: translate(calc(var(--dx) * 60px), 110vh) rotate(var(--spin));
    opacity: 0.6;
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
