<script setup lang="ts">
/**
 * 开场演出每页幻灯片上的小演示（shared/intro.ts 的 IntroDemo）。全是假的画面：不连 AI 服务、不碰文件，
 * 只是把这个功能「演」一遍让人一眼看懂。换页时整个组件重建（:key），动画从头放。
 *
 * - terminal：任务页那样一步步做事：读、搜、改、跑测试 ✓（打字时发 type 音效）
 * - diff：等你同意的改动，按钮按下去；下面一条分支线跑完测试合回去
 * - memory：你说「我不喝咖啡」，她说「记住了」，一张记忆卡片飞进记忆盒
 * - voice：「你好艾拉」的声波；一个报错窗口弹出来，她问要不要帮忙
 * - works：作品、日记、周报三张卡片翻进来
 * - ext：终端里打 aila，扩展、本事、MCP 连上来
 * - stars：一片转着的星星，几颗亮起来
 */
import { t } from '../i18n'
import { onBeforeUnmount, onMounted, ref } from 'vue'
import type { IntroDemo } from '../../../shared/intro'
import BrandMark from '../theme/BrandMark.vue'

const props = defineProps<{ demo: IntroDemo }>()
const emit = defineEmits<{ sfx: [name: 'type' | 'click'] }>()

// ---------- terminal：一行行出来，第一行是打出来的 ----------
const typed = ref('')
const steps = ref(0)
const timers: number[] = []
const later = (ms: number, fn: () => void): void => void timers.push(window.setTimeout(fn, ms))

// ---------- stars：画布上转的星星 ----------
const canvas = ref<HTMLCanvasElement | null>(null)
let raf = 0

function runTerminal(): void {
  const cmd = t('修好登录页点了没反应的问题')
  ;[...cmd].forEach((_, i) =>
    later(300 + i * 70, () => {
      typed.value = cmd.slice(0, i + 1)
      emit('sfx', 'type')
    })
  )
  const base = 300 + cmd.length * 70 + 300
  for (let i = 1; i <= 5; i++) later(base + i * 520, () => (steps.value = i))
}

function runStars(): void {
  const c = canvas.value
  if (!c) return
  const ctx = c.getContext('2d')
  if (!ctx) return
  const w = (c.width = c.clientWidth * devicePixelRatio)
  const h = (c.height = c.clientHeight * devicePixelRatio)
  const stars = Array.from({ length: 140 }, (_, i) => ({
    r: Math.sqrt(Math.random()) * Math.min(w, h) * 0.46,
    a: Math.random() * Math.PI * 2,
    z: Math.random(),
    lit: i % 17 === 0
  }))
  const begin = performance.now()
  const draw = (now: number): void => {
    const time = (now - begin) / 1000
    ctx.clearRect(0, 0, w, h)
    for (const s of stars) {
      const a = s.a + time * (0.12 + s.z * 0.1)
      const x = w / 2 + Math.cos(a) * s.r
      const y = h / 2 + Math.sin(a) * s.r * 0.55
      const glow = s.lit && time > 1.2 ? 0.6 + Math.sin(time * 4 + s.a) * 0.4 : 0
      ctx.beginPath()
      ctx.fillStyle = glow ? `rgba(103, 232, 249, ${0.5 + glow / 2})` : `rgba(79, 107, 237, ${0.35 + s.z * 0.5})`
      ctx.arc(x, y, (1.2 + s.z * 2.2 + glow * 3) * devicePixelRatio, 0, Math.PI * 2)
      ctx.fill()
    }
    raf = requestAnimationFrame(draw)
  }
  raf = requestAnimationFrame(draw)
}

onMounted(() => {
  if (props.demo === 'terminal') runTerminal()
  if (props.demo === 'stars') runStars()
  if (props.demo === 'diff') later(2600, () => emit('sfx', 'click'))
})

onBeforeUnmount(() => {
  timers.forEach(clearTimeout)
  cancelAnimationFrame(raf)
})
</script>

<template>
  <!-- 不把演示名当 class：内层也用了 voice、diff、memory 这些名字，会被套两遍样式 -->
  <div class="demo" :data-demo="demo">
    <!-- 欢迎：大字标，几点光慢慢飘 -->
    <div v-if="demo === 'welcome'" class="welcome">
      <BrandMark :size="120" :tile="false" />
      <span v-for="i in 12" :key="i" class="mote" :style="{ left: `${(i * 37) % 100}%`, animationDelay: `${(i % 6) * 0.4}s` }"></span>
    </div>

    <!-- 任务：终端里一步步做 -->
    <div v-else-if="demo === 'terminal'" class="term">
      <div class="term-bar"><i></i><i></i><i></i><span>aila</span></div>
      <p class="cmd">&gt; {{ typed }}<span class="caret">▍</span></p>
      <p v-if="steps >= 1" class="step">{{ t('读 src/pages/Login.vue') }}</p>
      <p v-if="steps >= 2" class="step">{{ t('搜「handleSubmit」') }}</p>
      <p v-if="steps >= 3" class="step edit">{{ t('改 src/pages/Login.vue') }} <b class="add">+3</b> <b class="del">−1</b></p>
      <p v-if="steps >= 4" class="step">{{ t('跑 npm test') }}</p>
      <p v-if="steps >= 5" class="step ok">✓ {{ t('24 个测试全过') }}</p>
    </div>

    <!-- 权限与分支：等你同意的改动；分支跑完测试合回去 -->
    <div v-else-if="demo === 'diff'" class="diff-demo">
      <div class="diff">
        <div class="diff-head">{{ t('要改 src/api/user.ts，可以吗？') }}</div>
        <pre><span class="del">-  return fetch(url)</span><span class="add">+  return fetch(url, { signal })</span></pre>
        <div class="btns"><span class="yes">{{ t('同意') }}</span><span class="no">{{ t('不要') }}</span></div>
      </div>
      <svg class="branch" viewBox="0 0 320 70" aria-hidden="true">
        <path class="main-line" d="M10 50 H310" />
        <path class="side-line" d="M60 50 C90 50 90 18 120 18 H220 C250 18 250 50 280 50" />
        <circle cx="60" cy="50" r="5" class="dot" />
        <circle cx="170" cy="18" r="5" class="dot side" />
        <circle cx="280" cy="50" r="6" class="dot merged" />
        <text x="150" y="10" class="tag">✓ {{ t('测试全过') }}</text>
      </svg>
    </div>

    <!-- 对话与记忆 -->
    <div v-else-if="demo === 'memory'" class="memory">
      <div class="bubble me">{{ t('我不喝咖啡') }}</div>
      <div class="bubble her">{{ t('记住了。') }}</div>
      <div class="mem-card">{{ t('不喝咖啡') }}</div>
      <div class="mem-box">{{ t('记忆') }}<b>+1</b></div>
    </div>

    <!-- 语音与屏幕 -->
    <div v-else-if="demo === 'voice'" class="voice">
      <div class="wave">
        <span v-for="i in 18" :key="i" :style="{ animationDelay: `${(i % 9) * 0.07}s` }"></span>
        <em>「{{ t('你好艾拉') }}」</em>
      </div>
      <div class="err-win">
        <div class="err-bar">Terminal</div>
        <code>TypeError: Cannot read properties of undefined (reading 'id')</code>
        <div class="err-ask">{{ t('要帮忙吗？') }}</div>
      </div>
    </div>

    <!-- 作品、日记、周报 -->
    <div v-else-if="demo === 'works'" class="works">
      <div class="w-card" style="animation-delay: 0.2s"><b>{{ t('作品') }}</b><span>{{ t('一个会躲鼠标的小方块') }}</span></div>
      <div class="w-card" style="animation-delay: 0.5s"><b>{{ t('日记') }}</b><span>{{ t('今天陪你修了三个 bug……') }}</span></div>
      <div class="w-card" style="animation-delay: 0.8s"><b>{{ t('回顾') }}</b><span>{{ t('这周做完 12 件事') }}</span></div>
    </div>

    <!-- 终端和扩展 -->
    <div v-else-if="demo === 'ext'" class="ext">
      <code class="shell">$ aila "{{ t('给 README 加个目录') }}"</code>
      <div class="chips">
        <span style="animation-delay: 0.15s">{{ t('扩展') }}</span>
        <span style="animation-delay: 0.3s">{{ t('本事') }}</span>
        <span style="animation-delay: 0.45s">MCP</span>
      </div>
    </div>

    <!-- 代码星图 -->
    <canvas v-else-if="demo === 'stars'" ref="canvas" class="stars"></canvas>
  </div>
</template>

<style scoped>
.demo {
  position: relative;
  height: 230px;
  margin-top: 22px;
}

/* 欢迎 */
.welcome {
  position: relative;
  display: grid;
  place-items: center;
  height: 100%;
  border-radius: 14px;
  background: radial-gradient(circle at 50% 40%, #2d2350, #151026 70%);
  overflow: hidden;
  animation: pop 0.6s cubic-bezier(0.2, 1.3, 0.4, 1) both;
}

.mote {
  position: absolute;
  bottom: -10px;
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: #67e8f9;
  box-shadow: 0 0 8px #67e8f9;
  animation: float 3.6s ease-in infinite;
}

/* 终端 */
.term {
  height: 100%;
  box-sizing: border-box;
  padding: 0 0 12px;
  border-radius: 12px;
  background: #151823;
  color: #d7dbe6;
  font: 13.5px/1.75 'Cascadia Code', Consolas, monospace;
  overflow: hidden;
  box-shadow: 0 10px 30px rgba(20, 24, 40, 0.18);
}

.term-bar {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 12px;
  background: #1e2230;
  color: #8a90a3;
  font-size: 12px;
}

.term-bar i {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: #3a3f52;
}

.term-bar span {
  margin-left: 8px;
}

.term p {
  margin: 0;
  padding: 0 16px;
}

.cmd {
  color: #c4b5fd;
  padding-top: 8px !important;
}

.caret {
  color: #67e8f9;
  animation: blink 0.9s steps(1) infinite;
}

.step {
  animation: rise 0.3s ease-out both;
}

.step::before {
  content: '› ';
  color: #596075;
}

.add {
  color: #86efac;
  font-weight: 400;
}

.del {
  color: #fca5a5;
  font-weight: 400;
}

.ok {
  color: #86efac;
}

/* 权限与分支 */
.diff-demo {
  display: grid;
  gap: 14px;
}

.diff {
  border: 1px solid #d9dce6;
  border-radius: 10px;
  background: #fff;
  overflow: hidden;
  animation: rise 0.4s ease-out both;
}

.diff-head {
  padding: 8px 12px;
  border-bottom: 1px solid #e6e8ef;
  color: #4a5063;
  font-size: 13px;
}

.diff pre {
  margin: 0;
  padding: 8px 12px;
  font: 13px/1.6 'Cascadia Code', Consolas, monospace;
}

.diff .del {
  display: block;
  background: #fdecec;
  color: #b42318;
}

.diff .add {
  display: block;
  background: #e9f8ee;
  color: #157f3d;
}

.btns {
  display: flex;
  gap: 8px;
  padding: 8px 12px 10px;
}

.btns span {
  padding: 4px 14px;
  border: 1px solid #cfd3df;
  border-radius: 6px;
  font-size: 13px;
}

.btns .yes {
  border-color: #4f6bed;
  background: #4f6bed;
  color: #fff;
  animation: press 0.35s ease-out 2.6s both;
}

.branch {
  width: 100%;
  height: 70px;
}

.main-line,
.side-line {
  fill: none;
  stroke-width: 3;
  stroke-linecap: round;
}

.main-line {
  stroke: #c9cede;
}

.side-line {
  stroke: #4f6bed;
  stroke-dasharray: 320;
  stroke-dashoffset: 320;
  animation: draw 1.6s ease-in-out 0.6s forwards;
}

.dot {
  fill: #fff;
  stroke: #9aa1b5;
  stroke-width: 2;
}

.dot.side {
  stroke: #4f6bed;
}

.dot.merged {
  stroke: #16a34a;
  fill: #dcfce7;
  opacity: 0;
  animation: fade-in 0.3s ease-out 2.2s forwards;
}

.tag {
  fill: #16a34a;
  font-size: 11px;
  opacity: 0;
  animation: fade-in 0.3s ease-out 1.8s forwards;
}

/* 记忆 */
.memory {
  position: relative;
  height: 100%;
}

.bubble {
  position: absolute;
  max-width: 60%;
  padding: 8px 14px;
  border-radius: 14px;
  font-size: 15px;
  animation: rise 0.35s ease-out both;
}

.bubble.me {
  right: 8%;
  top: 8px;
  background: #4f6bed;
  color: #fff;
}

.bubble.her {
  left: 4%;
  top: 62px;
  background: #fff;
  border: 1px solid #d9dce6;
  animation-delay: 0.9s;
}

.mem-card {
  position: absolute;
  left: 12%;
  top: 120px;
  padding: 6px 12px;
  border-radius: 8px;
  background: #efeafe;
  color: #5b3fd0;
  font-size: 13px;
  animation: fly 1.3s cubic-bezier(0.5, 0, 0.3, 1) 1.6s both;
}

.mem-box {
  position: absolute;
  right: 6%;
  bottom: 10px;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 16px;
  border: 1px dashed #b9a4ff;
  border-radius: 10px;
  color: #5b3fd0;
  font-size: 14px;
}

.mem-box b {
  opacity: 0;
  color: #16a34a;
  animation: fade-in 0.3s ease-out 2.9s forwards;
}

/* 语音与屏幕 */
.voice {
  display: grid;
  grid-template-columns: 1fr 1.3fr;
  gap: 18px;
  align-items: center;
  height: 100%;
}

.wave {
  display: flex;
  align-items: center;
  gap: 4px;
  height: 90px;
  position: relative;
}

.wave span {
  width: 6px;
  height: 20%;
  border-radius: 3px;
  background: linear-gradient(#67e8f9, #a78bfa);
  animation: bar 0.7s ease-in-out infinite alternate;
}

.wave em {
  position: absolute;
  bottom: -26px;
  left: 0;
  color: #4a5063;
  font-style: normal;
  font-size: 14px;
}

.err-win {
  position: relative;
  border: 1px solid #d9dce6;
  border-radius: 8px;
  background: #1b1d26;
  color: #f87171;
  animation: pop 0.4s cubic-bezier(0.2, 1.4, 0.4, 1) 1.2s both;
}

.err-bar {
  padding: 4px 10px;
  background: #2a2d3a;
  color: #9aa1b5;
  font-size: 11px;
}

.err-win code {
  display: block;
  padding: 10px 12px 14px;
  font: 12px/1.5 'Cascadia Code', Consolas, monospace;
  text-decoration: underline wavy #f87171;
}

.err-ask {
  position: absolute;
  right: -8px;
  bottom: -30px;
  padding: 6px 12px;
  border-radius: 12px;
  background: #fff;
  border: 1px solid #d9dce6;
  color: #1f2433;
  font-size: 13px;
  animation: pop 0.35s cubic-bezier(0.2, 1.4, 0.4, 1) 2.2s both;
}

/* 作品、日记、周报 */
.works {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  align-items: start;
  gap: 14px;
  height: 100%;
  perspective: 800px;
}

.w-card {
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-height: 120px;
  padding: 16px;
  border-radius: 12px;
  background: #fff;
  border: 1px solid #d9dce6;
  box-shadow: 0 8px 20px rgba(20, 24, 40, 0.06);
  animation: flip 0.6s ease-out both;
}

.w-card b {
  color: #4f6bed;
  font-size: 15px;
}

.w-card span {
  color: #4a5063;
  font-size: 14px;
  line-height: 1.6;
}

/* 终端和扩展 */
.ext {
  display: grid;
  gap: 22px;
  align-content: center;
  height: 100%;
}

.shell {
  padding: 12px 16px;
  border-radius: 10px;
  background: #151823;
  color: #c4b5fd;
  font: 14px 'Cascadia Code', Consolas, monospace;
  animation: rise 0.3s ease-out both;
}

.chips {
  display: flex;
  gap: 12px;
}

.chips span {
  padding: 8px 18px;
  border-radius: 999px;
  border: 1px solid #b9a4ff;
  background: #f3f0ff;
  color: #5b3fd0;
  font-size: 14px;
  animation: pop 0.35s cubic-bezier(0.2, 1.4, 0.4, 1) both;
}

/* 代码星图 */
.stars {
  width: 100%;
  height: 100%;
  border-radius: 14px;
  background: radial-gradient(circle at 50% 50%, #1c1f33, #0d0f18 75%);
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

@keyframes pop {
  from {
    opacity: 0;
    transform: scale(0.85);
  }
  to {
    opacity: 1;
    transform: none;
  }
}

@keyframes float {
  from {
    transform: translateY(0);
    opacity: 0;
  }
  20% {
    opacity: 1;
  }
  to {
    transform: translateY(-240px);
    opacity: 0;
  }
}

@keyframes blink {
  50% {
    opacity: 0;
  }
}

@keyframes press {
  50% {
    transform: scale(0.9);
    box-shadow: 0 0 0 6px rgba(79, 107, 237, 0.25);
  }
}

@keyframes draw {
  to {
    stroke-dashoffset: 0;
  }
}

@keyframes fade-in {
  to {
    opacity: 1;
  }
}

/* 记忆卡片从她的话下面飞进右下角的记忆盒，越飞越小，进去就没了 */
@keyframes fly {
  0% {
    opacity: 0;
    left: 12%;
    top: 120px;
    transform: none;
  }
  15% {
    opacity: 1;
    left: 14%;
    top: 116px;
  }
  85% {
    opacity: 1;
    transform: scale(0.7) rotate(6deg);
  }
  100% {
    opacity: 0;
    left: 80%;
    top: 180px;
    transform: scale(0.4) rotate(10deg);
  }
}

@keyframes bar {
  from {
    height: 15%;
  }
  to {
    height: 95%;
  }
}

@keyframes flip {
  from {
    opacity: 0;
    transform: rotateY(-70deg) translateY(10px);
  }
  to {
    opacity: 1;
    transform: none;
  }
}
</style>
