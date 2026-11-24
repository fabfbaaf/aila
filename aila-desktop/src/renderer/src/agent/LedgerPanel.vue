<script setup lang="ts">
/**
 * 上下文账本：下一步发给模型的上下文由哪几块组成。
 * 一条横条代表整个上下文窗口：开头固定的几块（每一步都一样，命中缓存）用青色，后面越往后越新；
 * 竖线是压缩线（快满时她会把前面的对话换成交接说明）。下面是每一步的缓存命中，和最占地方的几块。
 */
import { t } from '../i18n'
import { computed } from 'vue'
import type { AgentLedger } from '../../../shared/agent'
import { compact } from './fold'

const props = defineProps<{
  ledger: AgentLedger | null
  /** 每一步的输入和命中缓存（从会话日志里来） */
  steps: { prompt: number; hit: number }[]
  totals: { prompt: number; hit: number; completion: number; estimated: boolean }
}>()

const COLORS: Record<string, string> = {
  system: '#7c6cf0',
  tools: '#a78bfa',
  context: '#c4b5fd',
  summary: '#fbbf24',
  user: '#94a3b8',
  assistant: '#64748b'
}
const TOOL_COLORS = ['#22d3ee', '#67e8f9', '#06b6d4', '#a5f3fc', '#0e9fb8', '#5eead4']

function color(key: string, i: number): string {
  return COLORS[key] ?? TOOL_COLORS[i % TOOL_COLORS.length]
}

const segments = computed(() => {
  const l = props.ledger
  if (!l) return []
  let toolIndex = 0
  return l.segments.map((s) => ({
    ...s,
    color: color(s.key, s.key.startsWith('tool:') ? toolIndex++ : 0),
    width: `${Math.max(0.4, (s.tokens / l.window) * 100)}%`
  }))
})
const used = computed(() => (props.ledger ? Math.min(100, (props.ledger.prompt / props.ledger.window) * 100) : 0))
const fixedTokens = computed(() => props.ledger?.segments.filter((s) => s.fixed).reduce((n, s) => n + s.tokens, 0) ?? 0)
const lastHit = computed(() => (props.ledger && props.ledger.prompt ? Math.round((props.ledger.cache_hit / props.ledger.prompt) * 100) : 0))
const sessionHit = computed(() => (props.totals.prompt ? Math.round((props.totals.hit / props.totals.prompt) * 100) : 0))
const recentSteps = computed(() => props.steps.slice(-24))
</script>

<template>
  <div class="ledger">
    <template v-if="ledger">
      <div class="window">
        <div class="fill">
          <i v-for="s in segments" :key="s.key" :style="{ width: s.width, background: s.color }" :title="`${s.label} ${compact(s.tokens)}`"></i>
        </div>
        <span class="compact-line" :style="{ left: `${ledger.compact_at * 100}%` }" :title="t('压缩线')"></span>
      </div>
      <div class="mix" :title="t('已经用掉的部分由哪几块组成')">
        <i v-for="s in segments" :key="s.key" :style="{ flex: `${Math.max(s.tokens, 1)} 1 0`, background: s.color }"></i>
      </div>
      <p class="line">
        {{ t('下一步输入') }}{{ ledger.estimated ? t('约') : '' }} <b>{{ compact(ledger.prompt) }}</b> / {{ compact(ledger.window) }}{{ t('（{pct}%）', { pct: used.toFixed(0) }) }}
      </p>
      <p class="dim small">{{ t('到 {pct}% 会压缩；开头固定的 {fixed} 每一步都一样，命中缓存', { pct: Math.round(ledger.compact_at * 100), fixed: compact(fixedTokens) }) }}</p>
      <ul class="parts">
        <li v-for="s in segments" :key="s.key">
          <i :style="{ background: s.color }"></i><span class="name">{{ s.label }}</span><span class="num">{{ compact(s.tokens) }}</span>
        </li>
      </ul>
      <template v-if="ledger.biggest.length">
        <h4>{{ t('最占地方的') }}</h4>
        <ul class="parts big">
          <li v-for="(b, i) in ledger.biggest" :key="i"><span class="name" :title="b.label">{{ b.label }}</span><span class="num">{{ compact(b.tokens) }}</span></li>
        </ul>
      </template>
    </template>
    <p v-else class="dim">{{ t('还没有') }}</p>

    <template v-if="steps.length">
      <h4>{{ t('每一步命中缓存') }}</h4>
      <div class="spark" :title="t('上一步命中 {pct}%', { pct: lastHit })">
        <span v-for="(s, i) in recentSteps" :key="i" class="col">
          <i class="hit" :style="{ height: `${s.prompt ? (s.hit / s.prompt) * 100 : 0}%` }"></i>
        </span>
      </div>
      <p class="line">
        {{ t('这个会话：') }}<b>{{ sessionHit }}%</b>{{ t(' 的输入命中缓存') }}{{ totals.estimated ? t('（估算）') : '' }}
      </p>
      <p class="dim small">{{ t('输入 {p} · 命中 {h} · 输出 {o}', { p: compact(totals.prompt), h: compact(totals.hit), o: compact(totals.completion) }) }}</p>
    </template>
  </div>
</template>

<style scoped>
.window {
  position: relative;
  height: 14px;
  margin: 4px 0 6px;
  border-radius: 4px;
  background: var(--line);
  overflow: visible;
}

.fill {
  display: flex;
  height: 100%;
  overflow: hidden;
  border-radius: 4px;
}

.fill i {
  height: 100%;
  flex: none;
}

/* 用掉的部分放大看：各块的比例 */
.mix {
  display: flex;
  gap: 1px;
  height: 8px;
  margin-bottom: 4px;
  border-radius: 3px;
  overflow: hidden;
}

.mix i {
  min-width: 2px;
}

.compact-line {
  position: absolute;
  top: -3px;
  bottom: -3px;
  width: 2px;
  background: var(--warn);
}

.line {
  margin: 4px 0 0;
}

.line b {
  color: var(--text);
}

.small {
  margin: 2px 0 6px;
  font-size: 12px;
}

h4 {
  margin: 10px 0 4px;
  font-size: 12.5px;
  font-weight: 600;
  color: var(--dim);
}

.parts {
  margin: 0;
  padding: 0;
  list-style: none;
  font-size: 12.5px;
}

.parts li {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 1px 0;
}

.parts i {
  flex: none;
  width: 9px;
  height: 9px;
  border-radius: 2px;
}

.parts .name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.parts .num {
  color: var(--dim);
  font-family: Consolas, monospace;
}

.big .name {
  font-family: Consolas, 'Microsoft YaHei', monospace;
}

.spark {
  display: flex;
  align-items: flex-end;
  gap: 2px;
  height: 36px;
  padding: 2px 0;
  border-bottom: 1px solid var(--line);
}

.spark .col {
  position: relative;
  flex: 1;
  height: 100%;
  border-radius: 2px 2px 0 0;
  background: rgba(251, 191, 36, 0.3);
}

.spark .hit {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  border-radius: 2px 2px 0 0;
  background: var(--ok);
}
</style>
