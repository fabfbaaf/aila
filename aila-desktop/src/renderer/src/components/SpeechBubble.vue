<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'

const props = defineProps<{ text: string; visible: boolean }>()
const body = ref<HTMLDivElement>()

// 回复比气泡长时，始终显示最新的那几行
watch(
  () => props.text,
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
