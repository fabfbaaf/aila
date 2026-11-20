<script setup lang="ts">
/**
 * 她交的计划：目标、每个阶段（做什么、怎么验收、做到哪了）。
 * 还在等你决定、而且是最新的那份时，下面有「照这个做」「不用了」，可以勾「每个阶段做完停一下」。
 * 想改计划不用按钮：在输入框里直接说要怎么改，她会重新交一份。
 */
import { ref } from 'vue'
import Icon from '../theme/Icon.vue'
import type { PlanView } from './plan'

/** readOnly：这个任务现在是只读模式；批准时会先换成「先问再改」，按钮上写明 */
const props = defineProps<{ plan: PlanView; busy: boolean; readOnly?: boolean }>()
const emit = defineEmits<{ approve: [pauseEach: boolean]; reject: [] }>()
const pauseEach = ref(false)

const MARK: Record<string, string> = { pending: '', running: '进行中', done: '做完了', blocked: '卡住了' }

function label(): string {
  if (props.plan.decision === 'rejected') return '没要'
  if (props.plan.decision === 'proposed') return '等你决定'
  return props.plan.finished ? '全部做完了' : `做到第 ${(props.plan.current ?? 0) + 1}/${props.plan.stages.length} 阶段`
}
</script>

<template>
  <div class="plan-card" :class="plan.decision">
    <div class="plan-head">
      <Icon name="plan" :size="17" />
      <b>计划</b>
      <span class="plan-goal">{{ plan.goal }}</span>
      <span class="plan-state">{{ label() }}</span>
    </div>
    <ol class="stages">
      <li v-for="(s, i) in plan.stages" :key="i" :class="s.status">
        <div class="stage-title">
          <span class="num">{{ i + 1 }}</span>
          <b>{{ s.title }}</b>
          <span v-if="MARK[s.status]" class="mark">{{ MARK[s.status] }}</span>
        </div>
        <div v-if="s.goal && s.goal !== s.title" class="stage-goal">{{ s.goal }}</div>
        <div class="stage-verify">验收：{{ s.verify }}</div>
        <div v-if="s.summary" class="stage-summary">{{ s.summary }}</div>
      </li>
    </ol>
    <div v-if="plan.decision === 'proposed'" class="plan-actions">
      <label class="pause-each"><input v-model="pauseEach" type="checkbox" />每个阶段做完停一下，等我看过再接着做</label>
      <span class="hint">想改就直接在下面说，她会重新交一份</span>
      <button class="ghost" :disabled="busy" @click="emit('reject')">不用了</button>
      <button class="go" :disabled="busy" :title="readOnly ? '现在是只读模式，按计划做要改文件：会先换成「先问再改」' : ''" @click="emit('approve', pauseEach)">
        <Icon name="play" :size="13" :stroke="2" />{{ readOnly ? '换成「先问再改」并照这个做' : '照这个做' }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.plan-card {
  margin: 10px 0;
  border: 1px solid var(--accent-line);
  border-radius: var(--ui-r-l);
  background: var(--ui-brand-soft);
  overflow: hidden;
}

.plan-card.rejected {
  border-color: var(--line);
  background: var(--card);
  opacity: 0.7;
}

.plan-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  border-bottom: 1px solid var(--line);
  color: var(--ui-accent-hi);
}

.plan-goal {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text);
}

.plan-state {
  color: var(--dim);
  font-size: 12px;
  white-space: nowrap;
}

.stages {
  margin: 0;
  padding: 6px 14px 10px;
  list-style: none;
}

.stages li {
  padding: 8px 0;
  border-bottom: 1px dashed var(--line);
}

.stages li:last-child {
  border-bottom: none;
}

.stage-title {
  display: flex;
  align-items: center;
  gap: 8px;
}

.num {
  display: grid;
  place-items: center;
  flex: none;
  width: 20px;
  height: 20px;
  border: 1px solid var(--line-strong);
  border-radius: 50%;
  color: var(--dim);
  font-family: var(--ui-mono);
  font-size: 11px;
}

.running .num {
  border-color: var(--accent);
  background: var(--accent-bg);
  color: var(--ui-accent-hi);
}

.done .num {
  border-color: var(--ui-ok-line);
  background: var(--ui-ok-soft);
  color: var(--ok);
}

.blocked .num {
  border-color: var(--ui-warn-line);
  color: var(--warn);
}

.mark {
  font-size: 11.5px;
  color: var(--dim);
}

.running .mark {
  color: var(--ui-accent-hi);
}

.done .mark {
  color: var(--ok);
}

.blocked .mark {
  color: var(--warn);
}

.stage-goal,
.stage-verify,
.stage-summary {
  margin: 3px 0 0 28px;
  font-size: 12.5px;
  line-height: 1.55;
  color: var(--text-2);
}

.stage-verify {
  color: var(--dim);
  font-family: var(--ui-mono);
  font-size: 11.5px;
}

.stage-summary {
  color: var(--ok);
}

.blocked .stage-summary {
  color: var(--warn);
}

.plan-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 12px;
  padding: 10px 14px;
  border-top: 1px solid var(--line);
  background: var(--card);
}

.pause-each {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12.5px;
  color: var(--text-2);
  cursor: pointer;
}

.pause-each input {
  accent-color: var(--accent);
}

.hint {
  flex: 1;
  color: var(--ui-faint);
  font-size: 11.5px;
}

.ghost {
  padding: 5px 12px;
  border: 1px solid var(--line-strong);
  border-radius: var(--ui-r-s);
  background: transparent;
  color: var(--text-2);
  font-size: 12.5px;
}

.go {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 14px;
  border: none;
  border-radius: var(--ui-r);
  background: var(--ui-brand);
  color: #160f2a;
  font-weight: 600;
  font-size: 13px;
}

.go:disabled,
.ghost:disabled {
  opacity: 0.5;
}
</style>
