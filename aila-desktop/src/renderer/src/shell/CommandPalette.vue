<script setup lang="ts">
/** Ctrl+K 的搜索框：条目和排序在 palette.ts，这里只管显示、上下键选、回车执行 */
import { t } from '../i18n'
import { computed, nextTick, ref, watch } from 'vue'
import type { StoredMemory, StoredMessage } from '../../../shared/cloud'
import { bridge } from '../platform/bridge'
import { historyItems, memoryItems, searchStatic, staticItems, type PaletteAct, type PaletteItem } from './palette'

const emit = defineEmits<{ close: []; act: [act: PaletteAct] }>()

const query = ref('')
const active = ref(0)
const input = ref<HTMLInputElement | null>(null)
const messages = ref<StoredMessage[]>([])
const memories = ref<StoredMemory[]>([])
// 固定条目按现在的语言生成（英文界面时也能用英文搜）
const STATIC = computed(() => staticItems(t))

const items = computed<PaletteItem[]>(() => [
  ...searchStatic(STATIC.value, query.value),
  ...historyItems(messages.value, query.value),
  ...memoryItems(memories.value, query.value)
])

watch(query, () => (active.value = 0))

async function open(): Promise<void> {
  query.value = ''
  active.value = 0
  await nextTick()
  input.value?.focus()
  if (!bridge) return
  // 每次打开现取：刚聊的、刚记的也搜得到
  const [h, m] = await Promise.all([bridge.historyLoad(), bridge.memoryList()])
  messages.value = h
  memories.value = m
}

function run(item: PaletteItem | undefined): void {
  if (!item) return
  emit('act', item.act)
  emit('close')
}

function onKey(e: KeyboardEvent): void {
  if (e.key === 'Escape') {
    e.preventDefault()
    emit('close')
  } else if (e.key === 'ArrowDown') {
    e.preventDefault()
    active.value = Math.min(items.value.length - 1, active.value + 1)
    scrollActive()
  } else if (e.key === 'ArrowUp') {
    e.preventDefault()
    active.value = Math.max(0, active.value - 1)
    scrollActive()
  } else if (e.key === 'Enter' && !e.isComposing) {
    e.preventDefault()
    run(items.value[active.value])
  }
}

function scrollActive(): void {
  void nextTick(() => document.querySelector('.palette .row.on')?.scrollIntoView({ block: 'nearest' }))
}

defineExpose({ open })
</script>

<template>
  <div class="veil" @mousedown.self="emit('close')">
    <div class="palette" role="dialog" :aria-label="t('快捷跳转')">
      <input
        ref="input"
        v-model="query"
        :placeholder="t('去哪一页、做什么，或者搜聊过的话、她记得的事……')"
        spellcheck="false"
        :aria-label="t('搜索')"
        @keydown="onKey"
      />
      <ul v-if="items.length">
        <li
          v-for="(it, i) in items"
          :key="it.id"
          class="row"
          :class="{ on: i === active }"
          @mousemove="active = i"
          @click="run(it)"
        >
          <span class="group">{{ t(it.group) }}</span>
          <span class="title">{{ it.title }}</span>
          <span class="hint">{{ t(it.hint) }}</span>
        </li>
      </ul>
      <div v-else class="none">{{ t('没有对得上的。换个说法试试。') }}</div>
      <div class="foot">{{ t('↑↓ 选　Enter 打开　Esc 关掉') }}</div>
    </div>
  </div>
</template>

<style scoped>
.veil {
  position: fixed;
  inset: 0;
  z-index: 50;
  display: flex;
  justify-content: center;
  align-items: flex-start;
  padding-top: 12vh;
  background: rgba(8, 6, 18, 0.45);
}

.palette {
  width: min(620px, calc(100vw - 32px));
  max-height: 64vh;
  display: flex;
  flex-direction: column;
  border: 1px solid var(--ui-line);
  border-radius: var(--ui-r-lg, 12px);
  background: var(--ui-bg);
  box-shadow: 0 18px 60px rgba(0, 0, 0, 0.45);
  overflow: hidden;
}

input {
  padding: 14px 16px;
  border: none;
  border-bottom: 1px solid var(--ui-line);
  outline: none;
  background: transparent;
  color: var(--ui-text);
  font: inherit;
  font-size: 15px;
}

ul {
  margin: 0;
  padding: 6px;
  overflow-y: auto;
  list-style: none;
}

.row {
  display: grid;
  grid-template-columns: 44px minmax(0, 1fr) auto;
  gap: 10px;
  align-items: center;
  padding: 8px 10px;
  border-radius: var(--ui-r);
  cursor: pointer;
}

.row.on {
  background: var(--ui-sunken);
}

.group {
  color: var(--ui-muted);
  font-size: 11.5px;
}

.title {
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.hint {
  color: var(--ui-faint);
  font-size: 12px;
  white-space: nowrap;
}

.none {
  padding: 22px 16px;
  color: var(--ui-muted);
}

.foot {
  padding: 7px 14px;
  border-top: 1px solid var(--ui-line);
  color: var(--ui-faint);
  font-size: 11.5px;
}
</style>
