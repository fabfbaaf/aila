<script setup lang="ts">
/**
 * 对话记录：和她聊过的话，按天排，能搜。
 * 记录本来就存在本机（登录后经云端同步，这一页不改变同步），这里只是摆出来看；和她说话还是在桌面上。
 */
import { computed, onActivated, onBeforeUnmount, onMounted, ref } from 'vue'
import type { StoredMessage } from '../../../shared/cloud'
import { bridge } from '../platform/bridge'
import { cleanText, groupHistory, timeOf } from './historyView'

const messages = ref<StoredMessage[]>([])
const query = ref('')
const now = ref(new Date())
let off: (() => void) | null = null

const groups = computed(() => groupHistory(messages.value, query.value, now.value))
const total = computed(() => messages.value.filter((m) => !m.deleted).length)

async function load(): Promise<void> {
  if (!bridge) return
  messages.value = await bridge.historyLoad()
  now.value = new Date()
}

onMounted(() => {
  if (bridge) off = bridge.onHistoryChanged(() => void load())
})
onActivated(load)
onBeforeUnmount(() => off?.())
</script>

<template>
  <div class="history">
    <header class="bar">
      <b>对话</b>
      <input v-model="query" type="search" placeholder="搜聊过的话……" spellcheck="false" aria-label="搜对话记录" />
      <span class="count">{{ query ? `找到 ${groups.reduce((n, g) => n + g.items.length, 0)} 条` : `${total} 条` }}</span>
      <button class="talk" title="在桌面上打开对话框（Alt+Shift+A）" @click="bridge?.openChat()">和她说话</button>
    </header>

    <div class="scroll">
      <div v-if="!bridge" class="empty">对话记录只在桌面版里能看（npm run dev）。</div>
      <div v-else-if="!total" class="empty">还没聊过。双击桌面上的她，或者按 <kbd>Alt+Shift+A</kbd> 和她说话。</div>
      <div v-else-if="!groups.length" class="empty">没有含「{{ query }}」的话。</div>
      <section v-for="g in groups" :key="g.key" class="day">
        <h3>{{ g.label }}</h3>
        <div v-for="m in g.items" :key="m.id" class="msg" :class="m.role">
          <span class="who">{{ m.role === 'user' ? '你' : '她' }}</span>
          <span class="text">{{ cleanText(m.content) }}</span>
          <span class="time">{{ timeOf(m.createdAt) }}</span>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.history {
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  height: 100%;
}

.bar {
  display: flex;
  align-items: center;
  gap: 12px;
  min-height: 52px;
  box-sizing: border-box;
  padding: 0 18px;
  border-bottom: 1px solid var(--ui-line);
  background: var(--ui-bg);
}

.bar input {
  flex: 0 1 320px;
  min-width: 0;
  padding: 5px 10px;
  border: 1px solid var(--ui-field);
  border-radius: 8px;
  outline: none;
  background: var(--ui-sunken);
  color: var(--ui-text);
  font: inherit;
  font-size: 13px;
}

.bar input:focus {
  border-color: var(--ui-accent);
}

.count {
  flex: 1;
  color: var(--ui-muted);
  font-size: 12.5px;
}

.talk {
  padding: 5px 14px;
  border: 1px solid transparent;
  border-radius: var(--ui-r);
  background: var(--ui-brand);
  color: #160f2a;
  font: inherit;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  box-shadow: 0 3px 12px rgba(167, 139, 250, 0.22);
  transition: filter var(--ui-fast);
}

.talk:hover {
  filter: brightness(1.08);
}

.scroll {
  overflow-y: auto;
  padding: 8px 24px 32px;
}

.day h3 {
  position: sticky;
  top: 0;
  margin: 14px 0 6px;
  padding: 4px 0;
  background: var(--ui-sunken);
  color: var(--ui-muted);
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.06em;
}

.msg {
  display: grid;
  grid-template-columns: 28px minmax(0, 1fr) auto;
  gap: 10px;
  padding: 5px 0;
  max-width: 860px;
  line-height: 1.6;
}

.who {
  color: var(--ui-muted);
  font-size: 12.5px;
}

.msg.assistant .who {
  color: var(--ui-accent);
}

.text {
  white-space: pre-wrap;
  word-break: break-word;
}

.msg.user .text {
  color: var(--ui-text);
}

.time {
  color: var(--ui-faint);
  font-size: 11.5px;
  font-variant-numeric: tabular-nums;
}

.empty {
  padding: 40px 0;
  color: var(--ui-muted);
  text-align: center;
}

kbd {
  padding: 0 4px;
  border: 1px solid var(--ui-field);
  border-radius: 3px;
  font-size: 11px;
}
</style>
