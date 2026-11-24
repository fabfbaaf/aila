<script setup lang="ts">
import { t } from '../i18n'
import { onBeforeUnmount, onMounted, ref } from 'vue'

const emit = defineEmits<{ send: [text: string]; close: [] }>()
const text = ref('')
const input = ref<HTMLInputElement>()

function focus(): void {
  // 整个页面就是角色窗口，不能因为聚焦而滚动
  input.value?.focus({ preventScroll: true })
}

defineExpose({ focus })

function onKeydown(e: KeyboardEvent): void {
  if (e.key === 'Escape') {
    emit('close')
    return
  }
  // 中文输入法选字时按的回车不能当成发送
  if (e.key !== 'Enter' || e.isComposing || e.keyCode === 229) return
  const value = text.value.trim()
  if (!value) return
  emit('send', value)
  text.value = ''
}

// 切到别的窗口时，没打字就自动收起，不挡着她
function onWindowBlur(): void {
  if (!text.value.trim()) emit('close')
}

onMounted(() => {
  focus()
  window.addEventListener('blur', onWindowBlur)
})
onBeforeUnmount(() => window.removeEventListener('blur', onWindowBlur))
</script>

<template>
  <div class="chat-input" data-interactive>
    <input
      ref="input"
      v-model="text"
      maxlength="500"
      :placeholder="t('和艾拉说点什么…')"
      spellcheck="false"
      @keydown="onKeydown"
    />
    <span class="hint">{{ t('Enter 发送 · Esc 收起') }}</span>
  </div>
</template>

<style scoped>
.chat-input {
  flex-shrink: 0;
  box-sizing: border-box;
  width: min(272px, 100%);
  pointer-events: auto;
  padding: 6px 8px 4px;
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.94);
  box-shadow: 0 2px 12px rgba(40, 50, 80, 0.22);
}

input {
  width: 100%;
  box-sizing: border-box;
  border: none;
  outline: none;
  background: transparent;
  color: #3a3f55;
  font: inherit;
  font-size: 13px;
  line-height: 22px;
}

input::placeholder {
  color: #9aa0b4;
}

.hint {
  display: block;
  color: #a5abbe;
  font-size: 10.5px;
  text-align: right;
}
</style>
