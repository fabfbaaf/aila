<script setup lang="ts">
/**
 * 开场演出的全屏特效（shared/intro.ts 的 FxCommand）：铺满工作区的透明窗口，点不到，演完就关。
 *
 * - shatter：主窗口的玻璃碎成几十片，从裂纹往外飞出窗口、掉到屏幕底下；那块地方白光一闪
 * - trail：她跳过去的路上留一串青紫色的火花
 * - dust：落地时脚边扬起一小撮灰
 * - cursor：她借你鼠标时，光标外面一圈会呼吸的光（知道现在是她在动）
 * - charge：她放魔法蓄力时，满屏的光点往法阵聚过去；burst：法阵爆开，一圈光往外扩
 *
 * 没有预加载接口：主进程直接调 window.__fx(命令)（只是画画，不需要别的权限）。粒子的计算在 shared/intro.ts，能单测。
 */
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { particleAlive, seeded, spawnCharge, spawnDust, spawnShards, spawnSpark, stepParticle, type FxCommand, type Particle } from '../../../shared/intro'

const canvas = ref<HTMLCanvasElement | null>(null)
let particles: Particle[] = []
let flash: { x: number; y: number; width: number; height: number; t: number } | null = null
let cursor: { x: number; y: number } | null = null
/** 爆开的光圈：从哪儿、已经扩了多久（秒） */
let bursts: { x: number; y: number; t: number }[] = []
let raf = 0
let last = 0
const rand = seeded(20261124)

function command(c: FxCommand): void {
  if (c.fx === 'shatter') {
    particles.push(...spawnShards(c.rect, { x: c.x, y: c.y }, 46, rand))
    flash = { ...c.rect, t: 0 }
  } else if (c.fx === 'trail') {
    particles.push(spawnSpark({ x: c.x, y: c.y }, rand), spawnSpark({ x: c.x, y: c.y }, rand))
  } else if (c.fx === 'dust') {
    particles.push(...spawnDust({ x: c.x, y: c.y }, 18, rand))
  } else if (c.fx === 'charge') {
    particles.push(...spawnCharge({ x: c.x, y: c.y }, 70, rand))
  } else if (c.fx === 'burst') {
    bursts.push({ x: c.x, y: c.y, t: 0 })
  } else if (c.fx === 'cursor') {
    cursor = { x: c.x, y: c.y }
  } else if (c.fx === 'cursor-off') {
    cursor = null
  } else {
    particles = []
    flash = null
    cursor = null
    bursts = []
  }
  if (!raf) {
    last = performance.now()
    raf = requestAnimationFrame(frame)
  }
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
      ctx.fillStyle = `rgba(232, 236, 248, ${0.85 * fade})`
      ctx.fill()
      ctx.strokeStyle = `rgba(120, 130, 170, ${0.9 * fade})`
      ctx.lineWidth = 1
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
    ctx.arc(b.x, b.y, 20 + k * 620, 0, Math.PI * 2)
    ctx.strokeStyle = `rgba(200, 245, 255, ${0.85 * (1 - k)})`
    ctx.lineWidth = 6 * (1 - k) + 1
    ctx.shadowColor = '#67e8f9'
    ctx.shadowBlur = 24
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

  // 没东西可画了就停下，不白占 CPU
  raf = particles.length || flash || cursor || bursts.length ? requestAnimationFrame(frame) : 0
}

onMounted(() => {
  ;(window as unknown as { __fx: (c: FxCommand) => void }).__fx = command
})

onBeforeUnmount(() => cancelAnimationFrame(raf))
</script>

<template>
  <canvas ref="canvas" class="fx"></canvas>
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
</style>
