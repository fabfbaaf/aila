<script setup lang="ts">
/**
 * 对话：在主窗口里也能和她聊，长回答、代码、列表完整排好版；聊过的话按天排，能搜。
 *
 * 为什么这样设计：
 * - 对话只有一份，在桌面上的她那里跑（出声、动作、记忆、桌面上的气泡都在那）。这里打的字交给她去说（chat:say），
 *   她正在说的回复由角色窗口推过来（chat:live）实时显示；说完记进对话记录，这里从记录里显示。所以两边永远是同一段对话
 * - 桌面上的气泡只放短的、念出来的那部分；这里显示全文，按 Markdown 排（agent/markdown.ts：先转义再插固定标签，回复里的 HTML 不会执行）
 * - 她问「要不要……」时这里也有按钮，回答交回给她，只认她正在问的那一个
 */
import { t, lang, dateText } from '../i18n'
import { computed, nextTick, onActivated, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { LiveReply } from '../../../shared/chat'
import type { StoredMessage } from '../../../shared/cloud'
import { renderMarkdown } from '../agent/markdown'
import { bridge } from '../platform/bridge'
import { cleanText, groupHistory, timeOf } from './historyView'
import { chatQueryRequest } from './intents'

const messages = ref<StoredMessage[]>([])
const query = ref('')
const now = ref(new Date())
const live = ref<LiveReply | null>(null)
const draft = ref('')
const scroller = ref<HTMLElement | null>(null)
const input = ref<HTMLTextAreaElement | null>(null)
const offs: (() => void)[] = []

const groups = computed(() => groupHistory(messages.value, query.value, now.value, true))
const total = computed(() => messages.value.filter((m) => !m.deleted).length)
const replying = computed(() => live.value?.phase === 'waiting' || live.value?.phase === 'streaming')
/** 回复说完了，但还没记进对话记录（她可能还在念）：先接着显示这段 */
const showLive = computed(() => !!live.value && !query.value && (replying.value || live.value.phase === 'done' || !!live.value.ask))

async function load(): Promise<void> {
  if (!bridge) return
  const stick = nearBottom()
  messages.value = await bridge.historyLoad()
  now.value = new Date()
  // 说完的那句已经记进来了，就不用再单独显示
  if (live.value?.phase === 'done' && !live.value.ask) live.value = null
  if (stick) void toBottom()
}

function nearBottom(): boolean {
  const el = scroller.value
  return !el || el.scrollHeight - el.scrollTop - el.clientHeight < 120
}

async function toBottom(): Promise<void> {
  await nextTick()
  const el = scroller.value
  if (el) el.scrollTop = el.scrollHeight
}

function send(): void {
  const text = draft.value.trim()
  if (!text || !bridge) return
  draft.value = ''
  query.value = ''
  live.value = { phase: 'waiting', text: '', status: '', error: '', ask: null }
  bridge.sayToAila(text) // 她正在说的话会被打断，和在桌面上再说一句一样
  void toBottom()
}

function onKey(e: KeyboardEvent): void {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault()
    send()
  }
}

function answer(approved: boolean): void {
  const ask = live.value?.ask
  if (ask) bridge?.chatAnswer(ask.id, approved)
}

const WEEKDAYS_EN = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']

/** 每天的标题：中文用 historyView 排好的；英文自己排（Today、Yesterday、Mon, Nov 23） */
function dayTitle(g: { key: string; label: string }): string {
  if (lang.value !== 'en' || g.label === '今天' || g.label === '昨天') return t(g.label)
  const [y, m, d] = g.key.split('-').map(Number)
  const day = new Date(y, m - 1, d)
  const year = y === new Date().getFullYear() ? '' : `, ${y}`
  return `${WEEKDAYS_EN[day.getDay()]}, ${dateText(day)}${year}`
}

// Ctrl+K 里点了一段聊过的话：搜框里填上它
watch(
  chatQueryRequest,
  (q) => {
    if (!q) return
    query.value = q
    chatQueryRequest.value = ''
  },
  { immediate: true }
)

watch(() => live.value?.text, () => {
  if (nearBottom()) void toBottom()
})

onMounted(async () => {
  if (!bridge) return
  offs.push(
    bridge.onHistoryChanged(() => void load()),
    bridge.onChatLive((r) => (live.value = r))
  )
  const r = await bridge.chatLiveState()
  if (r && (r.phase === 'waiting' || r.phase === 'streaming' || r.ask)) live.value = r
  await load()
  void toBottom()
})
onActivated(async () => {
  await load()
  void toBottom()
  input.value?.focus()
})
onBeforeUnmount(() => offs.forEach((off) => off()))
</script>

<template>
  <div class="history">
    <header class="bar">
      <b>{{ t('对话') }}</b>
      <input v-model="query" type="search" :placeholder="t('搜聊过的话……')" spellcheck="false" :aria-label="t('搜对话记录')" />
      <span class="count">{{ query ? t('找到 {n} 条', { n: groups.reduce((n, g) => n + g.items.length, 0) }) : t('{n} 条', { n: total }) }}</span>
      <button class="ghost" :title="t('在桌面上打开对话框（Alt+Shift+A）')" @click="bridge?.openChat()">{{ t('在桌面上说') }}</button>
    </header>

    <div ref="scroller" class="scroll">
      <div v-if="!bridge" class="empty">{{ t('对话只在桌面版里能用（npm run dev）。') }}</div>
      <div v-else-if="!total && !showLive" class="empty">{{ t('还没聊过。在下面打字，或者双击桌面上的她、按') }} <kbd>Alt+Shift+A</kbd>。</div>
      <div v-else-if="query && !groups.length" class="empty">{{ t('没有含「{q}」的话。', { q: query }) }}</div>
      <section v-for="g in groups" :key="g.key" class="day">
        <h3>{{ dayTitle(g) }}</h3>
        <div v-for="m in g.items" :key="m.id" class="msg" :class="m.role">
          <span class="who">{{ m.role === 'user' ? t('你') : t('她') }}</span>
          <!-- eslint-disable-next-line vue/no-v-html -- renderMarkdown 先整段转义，只插固定标签 -->
          <div v-if="m.role === 'assistant'" class="text md" v-html="renderMarkdown(cleanText(m.content))" />
          <span v-else class="text plain">{{ m.content }}</span>
          <span class="time">{{ timeOf(m.createdAt) }}</span>
        </div>
      </section>

      <div v-if="showLive && live" class="msg assistant live">
        <span class="who">{{ t('她') }}</span>
        <div class="text">
          <!-- eslint-disable-next-line vue/no-v-html -- 同上 -->
          <div v-if="live.text" class="md" v-html="renderMarkdown(cleanText(live.text))" />
          <div v-if="live.status" class="status">{{ live.status }}</div>
          <div v-else-if="live.phase === 'waiting'" class="status">{{ t('在想……') }}</div>
          <div v-if="live.ask" class="ask">
            <div class="ask-head">{{ t('要我{action}吗？', { action: live.ask.action }) }}<span class="skill">{{ live.ask.skill }}</span></div>
            <pre class="ask-detail">{{ live.ask.detail }}</pre>
            <div class="ask-btns">
              <button class="primary" @click="answer(true)">{{ t('好') }}</button>
              <button class="ghost" @click="answer(false)">{{ t('不要') }}</button>
            </div>
          </div>
        </div>
        <span class="time" />
      </div>
      <div v-if="live?.phase === 'error' && !query" class="error">{{ t('没说完：{why}。可以再说一次。', { why: live.error || t('出错了') }) }}</div>
    </div>

    <footer v-if="bridge" class="composer">
      <textarea
        ref="input"
        v-model="draft"
        rows="1"
        :placeholder="t('和她说点什么……（Enter 发送，Shift+Enter 换行）')"
        :aria-label="t('和她说的话')"
        @keydown="onKey"
      />
      <button v-if="replying" class="ghost" :title="t('让她别说了')" @click="bridge?.chatStop()">{{ t('停下') }}</button>
      <button class="primary" :disabled="!draft.trim()" @click="send">{{ t('发送') }}</button>
    </footer>
  </div>
</template>

<style scoped>
.history {
  display: grid;
  grid-template-rows: auto minmax(0, 1fr) auto;
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
  white-space: nowrap;
}

.primary,
.ghost {
  flex-shrink: 0;
  padding: 5px 14px;
  border-radius: var(--ui-r);
  font: inherit;
  font-size: 13px;
  cursor: pointer;
  white-space: nowrap;
  transition: filter var(--ui-fast);
}

.primary {
  border: 1px solid transparent;
  background: var(--ui-brand);
  color: #160f2a;
  font-weight: 600;
  box-shadow: 0 3px 12px rgba(167, 139, 250, 0.22);
}

.primary:disabled {
  opacity: 0.45;
  cursor: default;
  box-shadow: none;
}

.ghost {
  border: 1px solid var(--ui-field);
  background: transparent;
  color: var(--ui-text);
}

.primary:not(:disabled):hover,
.ghost:hover {
  filter: brightness(1.08);
}

.scroll {
  overflow-y: auto;
  padding: 8px 24px 24px;
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
  min-width: 0;
  word-break: break-word;
}

.text.plain {
  white-space: pre-wrap;
}

.time {
  color: var(--ui-faint);
  font-size: 11.5px;
  font-variant-numeric: tabular-nums;
}

.md :deep(p) {
  margin: 0 0 6px;
}

.md :deep(h4) {
  margin: 12px 0 4px;
  font-size: 15px;
}

.md :deep(ul),
.md :deep(ol) {
  margin: 4px 0;
  padding-left: 22px;
}

.md :deep(code) {
  padding: 1px 5px;
  border-radius: var(--ui-r-xs);
  background: var(--ui-sunken);
  font-family: var(--ui-mono);
  font-size: 12.5px;
  color: var(--ui-code-inline);
}

.md :deep(pre) {
  margin: 8px 0;
  padding: 10px 12px;
  border: 1px solid var(--ui-line);
  border-radius: var(--ui-r);
  background: var(--ui-sunken);
  overflow-x: auto;
}

.md :deep(pre code) {
  padding: 0;
  background: none;
  color: var(--ui-code-text);
}

.md :deep(blockquote) {
  margin: 6px 0;
  padding-left: 12px;
  border-left: 3px solid var(--ui-line);
  color: var(--ui-muted);
}

.md :deep(.md-link) {
  color: var(--ui-cyan);
}

.status {
  color: var(--ui-muted);
  font-size: 13px;
}

.ask {
  margin-top: 8px;
  padding: 10px 12px;
  border: 1px solid var(--ui-accent);
  border-radius: var(--ui-r);
}

.ask-head .skill {
  margin-left: 8px;
  color: var(--ui-muted);
  font-size: 12px;
}

.ask-detail {
  max-height: 200px;
  margin: 6px 0 8px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
  font-family: var(--ui-mono);
  font-size: 12.5px;
  color: var(--ui-muted);
}

.ask-btns {
  display: flex;
  gap: 8px;
}

.error {
  max-width: 860px;
  margin: 6px 0 0 38px;
  color: var(--ui-bad, #f87171);
  font-size: 13px;
}

.composer {
  display: flex;
  align-items: flex-end;
  gap: 8px;
  padding: 10px 18px 14px;
  border-top: 1px solid var(--ui-line);
  background: var(--ui-bg);
}

.composer textarea {
  flex: 1;
  min-width: 0;
  max-height: 160px;
  min-height: 34px;
  box-sizing: border-box;
  padding: 7px 10px;
  border: 1px solid var(--ui-field);
  border-radius: 8px;
  outline: none;
  background: var(--ui-sunken);
  color: var(--ui-text);
  font: inherit;
  font-size: 13.5px;
  line-height: 1.5;
  resize: vertical;
  field-sizing: content;
}

.composer textarea:focus {
  border-color: var(--ui-accent);
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
