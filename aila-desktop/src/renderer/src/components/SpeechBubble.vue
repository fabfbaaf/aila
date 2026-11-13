<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import type { ConfirmAsk } from '../../../shared/chat'

const props = defineProps<{ text: string; visible: boolean; ask?: ConfirmAsk | null }>()
const emit = defineEmits<{ answer: [approved: boolean] }>()
const body = ref<HTMLDivElement>()

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
      <div ref="body" class="body">{{ text }}</div>
      <!-- 要用户确认的操作：具体内容由 AI 服务从参数生成，照原样显示 -->
      <div v-if="ask" class="ask" data-interactive>
        <div class="detail">
          <span v-if="ask.skill" class="skill">{{ ask.skill }}</span>
          {{ ask.detail }}
        </div>
        <div class="buttons">
          <button class="yes" @click="emit('answer', true)">好</button>
          <button class="no" @click="emit('answer', false)">不要</button>
        </div>
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

.buttons {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
  margin-top: 6px;
}

button {
  min-width: 56px;
  padding: 3px 12px;
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
