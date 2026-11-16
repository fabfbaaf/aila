<script setup lang="ts">
/** 执行轨迹：每一轮一条时间轴，模型请求、工具、等你确认各一行，看得出时间花在哪、每一步命中多少缓存 */
import { computed, ref } from 'vue'
import { compact } from './fold'
import { slowest, type TraceRow, type TraceTurn } from './trace'

const props = defineProps<{ turns: TraceTurn[] }>()
const showAll = ref(false)

/** 最新的在上面；默认只展开最近三轮 */
const shown = computed(() => {
  const latest = [...props.turns].reverse()
  return showAll.value ? latest : latest.slice(0, 3)
})

function pct(x: number, turn: TraceTurn): string {
  return `${Math.min(100, (x / Math.max(turn.duration, 0.001)) * 100)}%`
}

function width(r: TraceRow, turn: TraceTurn): string {
  return `max(3px, ${Math.min(100, ((r.end - r.start) / Math.max(turn.duration, 0.001)) * 100)}%)`
}

function secs(x: number): string {
  return x >= 10 ? `${x.toFixed(0)} 秒` : `${x.toFixed(1)} 秒`
}

function rowClass(r: TraceRow): string {
  if (r.kind !== 'tool') return r.kind
  return ['ok'].includes(r.status ?? '') ? 'tool' : ['error', 'denied'].includes(r.status ?? '') ? 'tool bad' : 'tool warn'
}

function ticks(turn: TraceTurn): number[] {
  return [0, 0.5, 1].map((f) => f * turn.duration)
}
</script>

<template>
  <div class="trace">
    <p v-if="!turns.length" class="empty">还没有做过事。给她一个任务，这里会按时间画出每一步。</p>
    <section v-for="turn in shown" :key="turn.key" class="turn">
      <header>
        <span class="ask">{{ turn.text }}</span>
        <span class="meta">
          {{ turn.running ? '正在做 · ' : '' }}{{ secs(turn.duration) }} · {{ turn.steps }} 步
          <template v-if="turn.prompt">
            · 输入 {{ compact(turn.prompt) }}（命中缓存 {{ Math.round((turn.hit / turn.prompt) * 100) }}%）· 输出 {{ compact(turn.completion) }}
          </template>
        </span>
      </header>
      <div class="axis">
        <span class="label"></span>
        <div class="ticks">
          <span v-for="(x, i) in ticks(turn)" :key="i" :style="{ left: pct(x, turn) }">{{ secs(x) }}</span>
        </div>
        <span class="stat"></span>
      </div>
      <div v-for="(r, i) in turn.rows" :key="i" class="row" :class="rowClass(r)">
        <span class="label" :title="r.label">{{ r.label }}</span>
        <div class="lane">
          <div class="bar" :style="{ left: pct(r.start, turn), width: width(r, turn) }">
            <i v-if="r.kind === 'model' && r.first" class="waiting" :style="{ width: `${(r.first / Math.max(r.end - r.start, 0.001)) * 100}%` }"></i>
          </div>
        </div>
        <span class="stat">
          {{ secs(r.end - r.start) }}
          <template v-if="r.tokens">· 命中 {{ r.tokens.prompt ? Math.round((r.tokens.hit / r.tokens.prompt) * 100) : 0 }}%</template>
        </span>
      </div>
      <footer v-if="!turn.running && turn.rows.length > 2">
        最慢：<span v-for="(r, i) in slowest(turn)" :key="i">{{ i ? '、' : '' }}{{ r.label }} {{ secs(r.end - r.start) }}</span>
      </footer>
    </section>
    <button v-if="turns.length > 3 && !showAll" class="more" @click="showAll = true">还有 {{ turns.length - 3 }} 轮更早的</button>
    <div class="legend">
      <span><i class="model"></i>她在想（浅色：等第一个字）</span>
      <span><i class="tool"></i>工具</span>
      <span><i class="wait"></i>等你确认</span>
      <span><i class="bad"></i>出错、被拒</span>
    </div>
  </div>
</template>

<style scoped>
.trace {
  max-width: 980px;
  margin: 0 auto;
  padding: 18px 24px 30px;
}

.empty {
  margin-top: 60px;
  text-align: center;
  color: var(--dim);
}

.turn {
  margin-bottom: 22px;
  padding: 12px 14px;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: var(--card);
}

header {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 12px;
  align-items: baseline;
  margin-bottom: 8px;
}

.ask {
  font-weight: 600;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.meta {
  color: var(--dim);
  font-size: 12.5px;
}

.axis,
.row {
  display: grid;
  grid-template-columns: minmax(120px, 200px) minmax(80px, 1fr) 110px;
  gap: 10px;
  align-items: center;
}

.ticks {
  position: relative;
  height: 16px;
  border-bottom: 1px solid var(--line);
}

.ticks span {
  position: absolute;
  transform: translateX(-50%);
  font-size: 11px;
  color: var(--dim);
  white-space: nowrap;
}

.ticks span:first-child {
  transform: none;
}

.ticks span:last-child {
  transform: translateX(-100%);
}

.row {
  min-height: 24px;
}

.label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12.5px;
}

.lane {
  position: relative;
  height: 14px;
  border-radius: 3px;
  background: rgba(255, 255, 255, 0.02);
}

.bar {
  position: absolute;
  top: 0;
  bottom: 0;
  overflow: hidden;
  border-radius: 3px;
  background: var(--accent);
}

.waiting {
  display: block;
  height: 100%;
  background: rgba(255, 255, 255, 0.35);
}

.row.tool .bar {
  background: #2dd4bf;
}

.row.tool.bad .bar {
  background: var(--bad);
}

.row.tool.warn .bar {
  background: var(--warn);
}

.row.wait .bar {
  background: repeating-linear-gradient(45deg, #64748b 0 4px, #334155 4px 8px);
}

.stat {
  font-size: 12px;
  color: var(--dim);
  text-align: right;
  white-space: nowrap;
}

footer {
  margin-top: 8px;
  font-size: 12.5px;
  color: var(--dim);
}

.more {
  display: block;
  margin: 0 auto 16px;
  padding: 4px 12px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: transparent;
}

.legend {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  font-size: 12px;
  color: var(--dim);
}

.legend i {
  display: inline-block;
  width: 12px;
  height: 8px;
  margin-right: 5px;
  border-radius: 2px;
}

.legend .model {
  background: var(--accent);
}

.legend .tool {
  background: #2dd4bf;
}

.legend .wait {
  background: repeating-linear-gradient(45deg, #64748b 0 3px, #334155 3px 6px);
}

.legend .bad {
  background: var(--bad);
}
</style>
