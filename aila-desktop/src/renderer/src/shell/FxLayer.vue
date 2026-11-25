<script setup lang="ts">
/**
 * 开场演出的全屏特效（shared/intro.ts 的 FxCommand）：铺满工作区的透明窗口，点不到，演完就关。
 * 破壁之前这个窗口压在她上面，所以这里画的玻璃在她前面——她就像被关在玻璃后面。
 *
 * - glass：主窗口前面那层玻璃：一点点冷色、边上一圈亮边、几道静止的反光，再有一道反光慢慢扫过去
 * - ripple：撞上、敲到玻璃时那里漾开几圈屏障的波纹，玻璃整体也亮一下
 * - sigil：她放魔法时玻璃上展开的法阵（圆环、刻度、六芒星、符文，自己画的几何），转着蓄力，最后爆开
 * - cracks：法阵爆开后发光的裂纹从法阵往外爬满玻璃
 * - shatter：玻璃碎成几十片，从裂纹往外飞、掉到屏幕底下；那块地方白光一闪（玻璃、法阵、裂纹一起没了）
 * - trail：她跳过去的路上留一串青紫色的火花；dust：落地时脚边扬起一小撮灰
 * - cursor：她借你鼠标时，光标外面一圈会呼吸的光（知道现在是她在动）
 * - charge：蓄力时满屏的光点往法阵聚过去；burst：法阵爆开，一圈光往外扩
 *
 * 没有预加载接口：主进程直接调 window.__fx(命令)（只是画画，不需要别的权限）。
 * 坐标都相对工作区（这个窗口的左上角）。粒子、裂纹的计算在 shared/intro.ts，能单测。
 */
import { onBeforeUnmount, onMounted, ref } from 'vue'
import {
  magicCracks,
  particleAlive,
  seeded,
  spawnCharge,
  spawnDust,
  spawnShards,
  spawnSpark,
  stepParticle,
  type FxCommand,
  type Particle
} from '../../../shared/intro'
import type { Rect } from '../../../shared/entrance'

const canvas = ref<HTMLCanvasElement | null>(null)
/** 法阵（DOM 里的 SVG，好让它整个转、放大） */
const sigil = ref<{ x: number; y: number; phase: 'charge' | 'burst'; key: number } | null>(null)
/** 裂纹（SVG，一条条爬出来） */
const cracks = ref<{ paths: string[]; x: number; y: number } | null>(null)
/** 屏障波纹（DOM，几圈环往外扩） */
const ripples = ref<{ id: number; x: number; y: number }[]>([])

/** 法阵上的刻度和符文（固定的几何，画一次） */
const TICKS = Array.from({ length: 36 }, (_, i) => {
  const a = (i / 36) * Math.PI * 2
  const r1 = i % 3 === 0 ? 80 : 84
  return { x1: Math.cos(a) * r1, y1: Math.sin(a) * r1, x2: Math.cos(a) * 90, y2: Math.sin(a) * 90 }
})
const STAR = [0, 1].map((k) =>
  [0, 1, 2]
    .map((i) => {
      const a = -Math.PI / 2 + (k * Math.PI) / 3 + (i * 2 * Math.PI) / 3
      return `${(Math.cos(a) * 70).toFixed(1)},${(Math.sin(a) * 70).toFixed(1)}`
    })
    .join(' ')
)
const RUNES = Array.from({ length: 8 }, (_, i) => {
  const a = (i / 8) * Math.PI * 2
  const cx = Math.cos(a) * 56
  const cy = Math.sin(a) * 56
  // 每个符文是三四笔短线，按编号变形，看着像字又不是任何真的文字
  const k = (i * 37) % 5
  return `M${cx - 5} ${cy - 6} L${cx + 5} ${cy - 6} M${cx} ${cy - 6} L${cx + (k - 2) * 2} ${cy + 6} M${cx - 4} ${cy + (k % 2 ? 2 : -1)} L${cx + 4} ${cy + (k % 2 ? -1 : 2)}`
})

let particles: Particle[] = []
let flash: { x: number; y: number; width: number; height: number; t: number } | null = null
let cursor: { x: number; y: number } | null = null
/** 爆开的光圈：从哪儿、已经扩了多久（秒） */
let bursts: { x: number; y: number; t: number }[] = []
/** 玻璃：盖在哪；hit：被撞、被敲后亮一下（秒，往下减） */
let glass: (Rect & { hit: number }) | null = null
let raf = 0
let last = 0
let seq = 0
const rand = seeded(20261124)

function command(c: FxCommand): void {
  switch (c.fx) {
    case 'glass':
      glass = { ...c.rect, hit: 0 }
      break
    case 'glass-off':
      glass = null
      cracks.value = null
      sigil.value = null
      break
    case 'ripple': {
      const id = ++seq
      ripples.value.push({ id, x: c.x, y: c.y })
      window.setTimeout(() => (ripples.value = ripples.value.filter((r) => r.id !== id)), 1300)
      if (glass) glass.hit = 0.5
      break
    }
    case 'sigil':
      sigil.value = { x: c.x, y: c.y, phase: c.phase, key: sigil.value?.key ?? ++seq }
      if (c.phase === 'burst') window.setTimeout(() => sigil.value?.phase === 'burst' && (sigil.value = null), 800)
      break
    case 'cracks':
      cracks.value = { x: c.x, y: c.y, paths: magicCracks({ x: c.x, y: c.y }, glass ?? { x: 0, y: 0, width: innerWidth, height: innerHeight }, 16, seeded(7)) }
      if (glass) glass.hit = 0.8
      break
    case 'shatter':
      particles.push(...spawnShards(c.rect, { x: c.x, y: c.y }, 60, rand))
      flash = { ...c.rect, t: 0 }
      glass = null
      cracks.value = null
      sigil.value = null
      break
    case 'trail':
      particles.push(spawnSpark({ x: c.x, y: c.y }, rand), spawnSpark({ x: c.x, y: c.y }, rand))
      break
    case 'dust':
      particles.push(...spawnDust({ x: c.x, y: c.y }, 18, rand))
      break
    case 'charge':
      particles.push(...spawnCharge({ x: c.x, y: c.y }, 70, rand))
      break
    case 'burst':
      bursts.push({ x: c.x, y: c.y, t: 0 })
      break
    case 'cursor':
      cursor = { x: c.x, y: c.y }
      break
    case 'cursor-off':
      cursor = null
      break
    default:
      particles = []
      flash = null
      cursor = null
      bursts = []
      glass = null
      cracks.value = null
      sigil.value = null
      ripples.value = []
  }
  if (!raf) {
    last = performance.now()
    raf = requestAnimationFrame(frame)
  }
}

/** 玻璃：一层很淡的冷色，亮边，几道静止的斜反光，一道慢慢扫过去的宽反光 */
function drawGlass(ctx: CanvasRenderingContext2D, g: Rect & { hit: number }, now: number, dt: number): void {
  g.hit = Math.max(0, g.hit - dt)
  const lit = g.hit * 0.5
  ctx.save()
  ctx.beginPath()
  ctx.rect(g.x, g.y, g.width, g.height)
  ctx.clip()
  ctx.fillStyle = `rgba(170, 205, 255, ${0.045 + lit * 0.12})`
  ctx.fillRect(g.x, g.y, g.width, g.height)
  // 静止的几道斜反光（左上一组、右边一条）
  const streak = (x0: number, w: number, a: number): void => {
    const x = g.x + g.width * x0
    ctx.beginPath()
    ctx.moveTo(x, g.y)
    ctx.lineTo(x + w, g.y)
    ctx.lineTo(x + w - g.height * 0.55, g.y + g.height)
    ctx.lineTo(x - g.height * 0.55, g.y + g.height)
    ctx.closePath()
    ctx.fillStyle = `rgba(235, 245, 255, ${a})`
    ctx.fill()
  }
  streak(0.14, 46, 0.05)
  streak(0.19, 12, 0.06)
  streak(0.8, 26, 0.035)
  // 扫过去的那道：每 5 秒一回
  const k = ((now / 5000) % 1) * 1.6 - 0.3
  const sx = g.x + g.width * k
  const grad = ctx.createLinearGradient(sx - 160, 0, sx + 160, 0)
  grad.addColorStop(0, 'rgba(255, 255, 255, 0)')
  grad.addColorStop(0.5, `rgba(235, 248, 255, ${0.09 + lit * 0.2})`)
  grad.addColorStop(1, 'rgba(255, 255, 255, 0)')
  ctx.fillStyle = grad
  ctx.beginPath()
  ctx.moveTo(sx - 160, g.y)
  ctx.lineTo(sx + 160, g.y)
  ctx.lineTo(sx + 160 - g.height * 0.4, g.y + g.height)
  ctx.lineTo(sx - 160 - g.height * 0.4, g.y + g.height)
  ctx.closePath()
  ctx.fill()
  ctx.restore()
  // 亮边
  ctx.strokeStyle = `rgba(190, 235, 255, ${0.28 + lit * 0.6})`
  ctx.lineWidth = 2
  ctx.shadowColor = '#67e8f9'
  ctx.shadowBlur = 10 + lit * 30
  ctx.strokeRect(g.x + 1, g.y + 1, g.width - 2, g.height - 2)
  ctx.shadowBlur = 0
}

function frame(now: number): void {
  const c = canvas.value
  const ctx = c?.getContext('2d')
  if (!c || !ctx) return
  const dpr = devicePixelRatio
  if (c.width !== innerWidth * dpr) {
    c.width = innerWidth * dpr
    c.height = innerHeight * dpr
  }
  const dt = Math.min(0.05, (now - last) / 1000)
  last = now
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  ctx.clearRect(0, 0, innerWidth, innerHeight)

  if (glass) drawGlass(ctx, glass, now, dt)

  if (flash) {
    flash.t += dt
    const a = Math.max(0, 0.75 - flash.t * 3)
    ctx.fillStyle = `rgba(255, 255, 255, ${a})`
    ctx.fillRect(flash.x, flash.y, flash.width, flash.height)
    if (a <= 0) flash = null
  }

  particles = particles.map((p) => stepParticle(p, dt)).filter((p) => particleAlive(p, innerHeight))
  for (const p of particles) {
    const fade = Math.max(0, Math.min(1, p.life / p.max))
    if (p.kind === 'shard' && p.shape) {
      ctx.save()
      ctx.translate(p.x, p.y)
      ctx.rotate(p.rot)
      ctx.beginPath()
      ctx.moveTo(p.shape[0][0], p.shape[0][1])
      ctx.lineTo(p.shape[1][0], p.shape[1][1])
      ctx.lineTo(p.shape[2][0], p.shape[2][1])
      ctx.closePath()
      ctx.fillStyle = `rgba(215, 235, 255, ${0.55 * fade})`
      ctx.fill()
      ctx.strokeStyle = `rgba(165, 243, 252, ${0.9 * fade})`
      ctx.lineWidth = 1.2
      ctx.stroke()
      // 玻璃上的一道反光
      ctx.beginPath()
      ctx.moveTo(p.shape[0][0] * 0.6, p.shape[0][1] * 0.6)
      ctx.lineTo(p.shape[1][0] * 0.6, p.shape[1][1] * 0.6)
      ctx.strokeStyle = `rgba(255, 255, 255, ${0.9 * fade})`
      ctx.stroke()
      ctx.restore()
    } else if (p.kind === 'charge') {
      // 越靠近法阵越亮、越白
      const near = 1 - fade
      const g = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, p.size * 4)
      g.addColorStop(0, `rgba(255, 255, 255, ${0.5 + near * 0.5})`)
      g.addColorStop(0.4, `rgba(103, 232, 249, ${0.4 + near * 0.5})`)
      g.addColorStop(1, 'rgba(167, 139, 250, 0)')
      ctx.fillStyle = g
      ctx.beginPath()
      ctx.arc(p.x, p.y, p.size * 4, 0, Math.PI * 2)
      ctx.fill()
    } else if (p.kind === 'spark') {
      const g = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, p.size * 3)
      g.addColorStop(0, `rgba(240, 250, 255, ${fade})`)
      g.addColorStop(0.35, `rgba(103, 232, 249, ${0.8 * fade})`)
      g.addColorStop(1, 'rgba(167, 139, 250, 0)')
      ctx.fillStyle = g
      ctx.beginPath()
      ctx.arc(p.x, p.y, p.size * 3, 0, Math.PI * 2)
      ctx.fill()
    } else {
      ctx.fillStyle = `rgba(190, 186, 205, ${0.45 * fade})`
      ctx.beginPath()
      ctx.arc(p.x, p.y, p.size * (1.6 - fade * 0.6), 0, Math.PI * 2)
      ctx.fill()
    }
  }

  for (const b of bursts) {
    b.t += dt
    const k = b.t / 0.8
    if (k >= 1) continue
    ctx.beginPath()
    ctx.arc(b.x, b.y, 20 + k * 720, 0, Math.PI * 2)
    ctx.strokeStyle = `rgba(200, 245, 255, ${0.85 * (1 - k)})`
    ctx.lineWidth = 8 * (1 - k) + 1
    ctx.shadowColor = '#67e8f9'
    ctx.shadowBlur = 28
    ctx.stroke()
    ctx.shadowBlur = 0
  }
  bursts = bursts.filter((b) => b.t < 0.8)

  if (cursor) {
    const pulse = 0.5 + Math.sin(now / 160) * 0.5
    ctx.beginPath()
    ctx.arc(cursor.x, cursor.y, 18 + pulse * 6, 0, Math.PI * 2)
    ctx.strokeStyle = `rgba(103, 232, 249, ${0.55 + pulse * 0.35})`
    ctx.lineWidth = 3
    ctx.shadowColor = '#67e8f9'
    ctx.shadowBlur = 14
    ctx.stroke()
    ctx.shadowBlur = 0
  }

  // 没东西可画了就停下，不白占 CPU（玻璃上那道反光一直在扫，盖着玻璃时一直画）
  raf = glass || particles.length || flash || cursor || bursts.length ? requestAnimationFrame(frame) : 0
}

onMounted(() => {
  ;(window as unknown as { __fx: (c: FxCommand) => void }).__fx = command
})

onBeforeUnmount(() => cancelAnimationFrame(raf))
</script>

<template>
  <canvas ref="canvas" class="fx"></canvas>
  <!-- 撞上、敲到玻璃：漾开的波纹 -->
  <div v-for="r in ripples" :key="r.id" class="barrier" :style="{ left: `${r.x}px`, top: `${r.y}px` }"><i></i><i></i><i></i></div>
  <!-- 法阵爆开后发光的裂纹 -->
  <svg v-if="cracks" class="cracks">
    <path v-for="(d, i) in cracks.paths" :key="i" :d="d" pathLength="1" :style="{ animationDelay: `${(i % 16) * 0.025}s` }" />
    <circle :cx="cracks.x" :cy="cracks.y" r="18" />
  </svg>
  <!-- 法阵 -->
  <svg
    v-if="sigil"
    :key="sigil.key"
    class="sigil"
    :class="sigil.phase"
    :style="{ left: `${sigil.x}px`, top: `${sigil.y}px` }"
    viewBox="-100 -100 200 200"
  >
    <g class="outer">
      <circle r="92" />
      <circle r="78" />
      <line v-for="(k, i) in TICKS" :key="i" :x1="k.x1" :y1="k.y1" :x2="k.x2" :y2="k.y2" />
    </g>
    <g class="inner">
      <polygon v-for="(pts, i) in STAR" :key="i" :points="pts" />
      <path v-for="(d, i) in RUNES" :key="'r' + i" :d="d" class="rune" />
      <circle r="34" />
      <circle r="6" class="core" />
    </g>
  </svg>
</template>

<style>
html,
body {
  margin: 0;
  background: transparent;
  overflow: hidden;
}
</style>

<style scoped>
.fx {
  position: fixed;
  inset: 0;
  width: 100vw;
  height: 100vh;
  pointer-events: none;
}

/* 屏障波纹：几圈青色的环往外扩，像撞在一层看不见的玻璃上 */
.barrier {
  position: fixed;
  width: 0;
  height: 0;
  pointer-events: none;
}

.barrier i {
  position: absolute;
  left: -90px;
  top: -90px;
  width: 180px;
  height: 180px;
  border-radius: 50%;
  border: 3px solid rgba(103, 232, 249, 0.9);
  box-shadow:
    0 0 22px rgba(103, 232, 249, 0.7),
    inset 0 0 18px rgba(167, 139, 250, 0.5);
  animation: barrier 1.1s ease-out both;
}

.barrier i:nth-child(2) {
  animation-delay: 0.12s;
}

.barrier i:nth-child(3) {
  animation-delay: 0.24s;
}

/* 法阵：外圈和里圈反着转，蓄力时慢慢放大变亮，爆开时一下撑大、变白、散掉 */
.sigil {
  position: fixed;
  width: 340px;
  height: 340px;
  margin: -170px 0 0 -170px;
  overflow: visible;
  pointer-events: none;
  filter: drop-shadow(0 0 6px #67e8f9) drop-shadow(0 0 14px rgba(167, 139, 250, 0.8));
}

.sigil circle,
.sigil line,
.sigil polygon,
.sigil path {
  fill: none;
  stroke: #a5f3fc;
  stroke-width: 1.6;
  stroke-linecap: round;
}

.sigil polygon {
  stroke: #c4b5fd;
}

.sigil .rune {
  stroke: #e9d5ff;
  stroke-width: 1.4;
}

.sigil .core {
  fill: #f0fdff;
}

.sigil .outer {
  animation: spin 6s linear infinite;
}

.sigil .inner {
  animation: spin 4s linear infinite reverse;
}

.sigil.charge {
  animation: sigil-in 2.6s cubic-bezier(0.2, 0.9, 0.3, 1) both;
}

.sigil.burst {
  animation: sigil-burst 0.75s ease-out both;
}

/* 裂纹：发光的青色，一条条从法阵爬出去 */
.cracks {
  position: fixed;
  inset: 0;
  width: 100vw;
  height: 100vh;
  pointer-events: none;
}

.cracks path {
  fill: none;
  stroke: #cffafe;
  stroke-width: 2.2;
  stroke-linejoin: round;
  stroke-dasharray: 1;
  stroke-dashoffset: 1;
  filter: drop-shadow(0 0 3px #67e8f9) drop-shadow(0 0 9px rgba(167, 139, 250, 0.9));
  animation: crawl 0.45s ease-out forwards;
}

.cracks circle {
  fill: rgba(240, 253, 255, 0.9);
  filter: drop-shadow(0 0 16px #67e8f9);
}

@keyframes barrier {
  from {
    opacity: 1;
    transform: scale(0.2);
  }
  to {
    opacity: 0;
    transform: scale(1.8);
  }
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

@keyframes sigil-in {
  0% {
    opacity: 0;
    transform: scale(0.2) rotate(-90deg);
  }
  35% {
    opacity: 1;
  }
  100% {
    opacity: 1;
    transform: scale(1) rotate(0deg);
  }
}

@keyframes sigil-burst {
  0% {
    opacity: 1;
    transform: scale(1);
  }
  30% {
    filter: drop-shadow(0 0 24px #fff) brightness(2.4);
  }
  100% {
    opacity: 0;
    transform: scale(2.4);
    filter: drop-shadow(0 0 30px #fff) brightness(3);
  }
}

@keyframes crawl {
  to {
    stroke-dashoffset: 0;
  }
}
</style>
