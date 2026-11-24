<script setup lang="ts">
/**
 * 手势层：摄像头预览（带手的骨架）+ 屏幕上的手势光标（保持进度圈）+ 手势说明。
 * 挂上就开摄像头，卸掉就关（v-if 控制）。认出来的动作通过 gesture 事件交给星图。
 */
import { t } from '../i18n'
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { classifyHand, type Point } from './classify'
import { GestureEngine, REACH, toScreen, type GestureEvent } from './GestureEngine'
import { HAND_BONES, HandTracker, TrackerStartError } from './HandTracker'

defineProps<{ shifted?: boolean }>()
const emit = defineEmits<{
  gesture: [ev: GestureEvent]
  /** 手势光标在窗口里的位置（窗口比例坐标），手不在画面里是 null */
  cursor: [p: Point | null]
  failed: [message: string]
}>()

const preview = ref<HTMLDivElement>()
const bones = ref<HTMLCanvasElement>()
const phase = ref<'starting' | 'on' | 'failed'>('starting')
const message = ref('')
const cursor = ref<Point | null>(null)
const label = ref('')
const progress = ref(0)
const holding = ref(false)

const tracker = new HandTracker()
const engine = new GestureEngine()
const SMOOTH = 0.45 // 光标跟手的快慢：小了拖沓，大了抖

tracker.onHands = (hands, now) => {
  drawBones(hands)
  const events = engine.update(hands.map(classifyHand), now)
  const s = engine.state
  const prev = cursor.value
  if (s.cursor) {
    const target = toScreen(s.cursor)
    cursor.value = prev
      ? { x: prev.x + (target.x - prev.x) * SMOOTH, y: prev.y + (target.y - prev.y) * SMOOTH }
      : target
  } else {
    cursor.value = null
  }
  label.value = s.label
  progress.value = s.progress
  // 手一直不在画面里就不用每帧报告（不然会把鼠标悬停的提示也清掉）
  if (cursor.value || prev) emit('cursor', cursor.value)
  for (const ev of events) {
    if (ev.type === 'grab') holding.value = true
    if (ev.type === 'release') holding.value = false
    if (ev.type === 'grab') emit('gesture', { ...ev, ...(cursor.value ?? toScreen(ev)) })
    else if (ev.type === 'rotate') emit('gesture', { ...ev, dx: ev.dx / REACH, dy: ev.dy / REACH })
    else emit('gesture', ev)
  }
}

function drawBones(hands: Point[][]): void {
  const canvas = bones.value
  const video = tracker.video
  if (!canvas || !video.videoWidth) return
  if (canvas.width !== video.videoWidth) {
    canvas.width = video.videoWidth
    canvas.height = video.videoHeight
  }
  const ctx = canvas.getContext('2d')!
  const { width: w, height: h } = canvas
  ctx.clearRect(0, 0, w, h)
  ctx.lineWidth = 3
  ctx.strokeStyle = 'rgba(125, 255, 239, 0.85)'
  ctx.fillStyle = '#ffffff'
  for (const lm of hands) {
    ctx.beginPath()
    for (const [a, b] of HAND_BONES) {
      ctx.moveTo(lm[a].x * w, lm[a].y * h)
      ctx.lineTo(lm[b].x * w, lm[b].y * h)
    }
    ctx.stroke()
    for (const p of lm) {
      ctx.beginPath()
      ctx.arc(p.x * w, p.y * h, 3, 0, Math.PI * 2)
      ctx.fill()
    }
  }
}

/** 进度圈：SVG 圆的周长 */
const RING = 2 * Math.PI * 22

onMounted(async () => {
  preview.value?.prepend(tracker.video)
  try {
    await tracker.start()
    phase.value = 'on'
  } catch (err) {
    phase.value = 'failed'
    message.value = err instanceof TrackerStartError ? err.message : t('摄像头打不开：{why}', { why: (err as Error).message })
    emit('failed', message.value)
  }
})

onBeforeUnmount(() => {
  tracker.stop()
  emit('cursor', null)
})
</script>

<template>
  <div class="cam" :class="{ shifted }">
    <div ref="preview" class="preview">
      <canvas ref="bones"></canvas>
      <div v-if="phase === 'starting'" class="cover">{{ t('正在打开摄像头、加载手势模型……') }}</div>
      <div v-else-if="phase === 'failed'" class="cover failed">{{ message }}</div>
    </div>
    <div class="cam-note">
      <i class="dot" :class="phase"></i>
      <template v-if="phase === 'on'">{{ t('摄像头开着 · 只在这台电脑上识别，不保存、不上传') }}</template>
      <template v-else-if="phase === 'starting'">{{ t('正在打开摄像头') }}</template>
      <template v-else>{{ t('摄像头没开') }}</template>
    </div>
    <ul class="cheats">
      <li><b>{{ t('剑指') }}</b>{{ t('唤出全图') }}</li>
      <li><b>{{ t('握拳') }}</b>{{ t('收起面板 / 关掉星图') }}</li>
      <li><b>{{ t('捏合') }}</b>{{ t('抓住星球，往面前拉展开代码') }}</li>
      <li><b>{{ t('张开手掌') }}</b>{{ t('移动旋转星图') }}</li>
      <li><b>{{ t('双手张开') }}</b>{{ t('拉开放大，合拢缩小') }}</li>
      <li><b>{{ t('双手合十') }}</b>{{ t('确认她准备好的修改') }}</li>
    </ul>
  </div>

  <div
    v-if="cursor && phase === 'on'"
    class="hand-cursor"
    :class="{ holding }"
    :style="{ left: `${cursor.x * 100}%`, top: `${cursor.y * 100}%` }"
  >
    <svg width="56" height="56" viewBox="0 0 56 56">
      <circle cx="28" cy="28" r="22" class="track" />
      <circle
        cx="28"
        cy="28"
        r="22"
        class="arc"
        :stroke-dasharray="RING"
        :stroke-dashoffset="RING * (1 - progress)"
        transform="rotate(-90 28 28)"
      />
      <circle cx="28" cy="28" :r="holding ? 7 : 4" class="core" />
    </svg>
    <div v-if="label" class="hand-label">{{ label }}</div>
  </div>
</template>

<style scoped>
.cam {
  position: absolute;
  right: 14px;
  bottom: 40px;
  width: 208px;
  z-index: 2;
  transition: right 0.25s;
}

.cam.shifted {
  right: 468px; /* 让开右边的全息面板 */
}

.preview {
  position: relative;
  width: 208px;
  height: 156px;
  overflow: hidden;
  border-radius: 8px;
  border: 1px solid rgba(90, 209, 255, 0.4);
  background: #02050c;
  box-shadow: 0 0 16px rgba(90, 209, 255, 0.18);
}

/* 预览像照镜子：左右翻过来，和手的动作方向一致 */
.preview :deep(video),
.preview canvas {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
  transform: scaleX(-1);
}

.preview :deep(video) {
  opacity: 0.55;
}

.cover {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 12px;
  text-align: center;
  color: #8fa4c8;
  font-size: 12px;
  background: rgba(2, 5, 12, 0.8);
}

.cover.failed {
  color: #ffb3b9;
}

.cam-note {
  margin-top: 4px;
  color: #8fa4c8;
  font-size: 11px;
}

.dot {
  display: inline-block;
  width: 7px;
  height: 7px;
  margin-right: 5px;
  border-radius: 50%;
  background: #6f7da8;
}

.dot.on {
  background: #ff4d5e;
  box-shadow: 0 0 6px #ff4d5e;
}

.cheats {
  margin: 6px 0 0;
  padding: 6px 8px;
  list-style: none;
  border-radius: 6px;
  background: rgba(8, 16, 32, 0.6);
  color: #8fa4c8;
  font-size: 11px;
  line-height: 1.7;
  pointer-events: none;
}

.cheats b {
  display: inline-block;
  min-width: 52px;
  font-weight: normal;
  color: #7dffef;
}

.hand-cursor {
  position: absolute;
  z-index: 3;
  width: 56px;
  height: 56px;
  margin: -28px 0 0 -28px;
  pointer-events: none;
}

.track {
  fill: none;
  stroke: rgba(125, 255, 239, 0.25);
  stroke-width: 2;
}

.arc {
  fill: none;
  stroke: #7dffef;
  stroke-width: 3;
  stroke-linecap: round;
  filter: drop-shadow(0 0 4px #7dffef);
}

.core {
  fill: #e8fffb;
  transition: r 0.15s;
}

.holding .track {
  stroke: rgba(255, 210, 122, 0.6);
}

.hand-label {
  position: absolute;
  top: 58px;
  left: 50%;
  transform: translateX(-50%);
  padding: 2px 8px;
  border-radius: 4px;
  white-space: nowrap;
  font-size: 12px;
  color: #dffcff;
  background: rgba(4, 10, 24, 0.75);
  border: 1px solid rgba(125, 255, 239, 0.35);
}
</style>
