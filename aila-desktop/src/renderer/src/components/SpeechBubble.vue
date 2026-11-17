<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { diffLines, type BubbleAsk } from './bubble'

const props = defineProps<{ text: string; visible: boolean; ask?: BubbleAsk | null; fullText?: string; expanded?: boolean }>()
const emit = defineEmits<{ answer: [value: string]; toggleFullText: [] }>()
const body = ref<HTMLDivElement>()

/** 具体内容按行显示；是代码改动（diff）时，加的行标绿、删的行标红 */
const detailLines = computed(() => diffLines(props.ask?.detail ?? ''))
const isDiff = computed(() => detailLines.value.some((l) => l.kind === 'hunk'))

// 回复比气泡长时，始终显示最新的那几行
watch(
  () => [props.text, props.ask],
  async () => {
    await nextTick()
    if (body.value) body.value.scrollTop = body.value.scrollHeight
  }
)
</script>

<template>
  <Transition name="bubble">
    <div v-if="visible" class="bubble">
      <div v-show="!expanded || !fullText || ask" ref="body" class="body">{{ text }}</div>
      <!-- 等用户回答：要确认的操作（具体内容由 AI 服务从参数生成，照原样显示）、屏幕上看到的报错 -->
      <div v-if="ask" class="ask" data-interactive>
        <div class="detail" :class="{ diff: isDiff }">
          <span v-if="ask.tag" class="skill">{{ ask.tag }}</span>
          <template v-if="isDiff">
            <div v-for="(l, i) in detailLines" :key="i" :class="['line', l.kind]">{{ l.text }}</div>
          </template>
          <template v-else>{{ ask.detail }}</template>
        </div>
        <div v-if="ask.note" class="note">{{ ask.note }}</div>
        <div class="buttons">
          <button
            v-for="b in ask.buttons"
            :key="b.value"
            :class="[b.value, { yes: b.primary }]"
            @click="emit('answer', b.value)"
          >
            {{ b.label }}
          </button>
        </div>
      </div>
      <div v-if="fullText && !ask" class="read-reply" data-interactive>
        <button type="button" :aria-expanded="expanded" aria-controls="bubble-full-reply" @click="emit('toggleFullText')">
          {{ expanded ? '收起全文' : '看全文' }}
        </button>
        <div v-show="expanded" id="bubble-full-reply" class="full-reply" role="region" aria-label="回复全文" tabindex="0">{{ fullText }}</div>
      </div>
    </div>
  </Transition>
</template>

<style scoped>
.bubble {
  position: absolute;
  top: 14px;
  left: 50%;
  transform: translateX(-50%);
  max-width: 250px;
  padding: 9px 14px;
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.95);
  color: #3a3f55;
  font-size: 13px;
  line-height: 1.55;
  box-shadow: 0 2px 10px rgba(40, 50, 80, 0.18);
  pointer-events: none;
  white-space: pre-wrap;
}

/* 最多 4 行（再多会挡住她的脸），多出来的往上滚 */
.body {
  max-height: calc(13px * 1.55 * 4);
  overflow: hidden;
}

.ask {
  margin-top: 6px;
  pointer-events: auto;
}

.read-reply {
  margin-top: 6px;
  text-align: right;
  pointer-events: auto;
}

.read-reply :focus-visible {
  outline: 2px solid #5b6cc4;
  outline-offset: 2px;
}

.full-reply {
  max-height: min(13em, 55vh);
  margin-top: 6px;
  padding: 5px 8px;
  border-radius: 8px;
  overflow-y: auto;
  overflow-wrap: anywhere;
  background: #eef1f7;
  text-align: left;
  user-select: text;
}

/* 网址、参数可能很长：任意位置换行，最多 5 行，多了可以滚动查看 */
.detail {
  max-height: calc(12px * 1.5 * 5);
  overflow-y: auto;
  padding: 5px 8px;
  border-radius: 8px;
  background: #eef1f7;
  color: #2b3045;
  font-family: Consolas, 'Microsoft YaHei', monospace;
  font-size: 12px;
  line-height: 1.5;
  word-break: break-all;
  user-select: text;
}

/* 代码改动：框高一点，按行着色 */
.detail.diff {
  max-height: calc(12px * 1.5 * 12);
  word-break: normal;
  overflow-wrap: anywhere;
}

.line.add {
  background: #e3f4e6;
  color: #1d6b32;
}

.line.del {
  background: #fbe5e5;
  color: #9b2c2c;
}

.line.hunk,
.line.file {
  color: #7a8099;
}

.skill {
  display: inline-block;
  margin-right: 4px;
  padding: 0 5px;
  border-radius: 4px;
  background: #dfe4f0;
  color: #4a5270;
  font-family: 'Microsoft YaHei', sans-serif;
  font-size: 11px;
}

.note {
  margin-top: 3px;
  color: #7a8099;
  font-size: 11px;
}

.buttons {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  justify-content: flex-end;
  margin-top: 6px;
}

button {
  min-width: 52px;
  padding: 3px 10px;
  border: 1px solid #c9cfdd;
  border-radius: 8px;
  background: #fff;
  color: #3a3f55;
  font: inherit;
  cursor: pointer;
}

button.yes {
  border-color: #5b6cc4;
  background: #5b6cc4;
  color: #fff;
}

button:hover {
  filter: brightness(0.95);
}

.bubble::after {
  content: '';
  position: absolute;
  bottom: -6px;
  left: 50%;
  margin-left: -6px;
  border: 6px solid transparent;
  border-bottom: 0;
  border-top-color: rgba(255, 255, 255, 0.95);
}

.bubble-enter-active,
.bubble-leave-active {
  transition:
    opacity 0.25s ease,
    transform 0.25s ease;
}

.bubble-enter-from,
.bubble-leave-to {
  opacity: 0;
  transform: translate(-50%, 6px);
}
</style>
