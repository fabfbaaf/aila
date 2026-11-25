<script setup lang="ts">
/** 一个工具调用：一行「做什么」+ 结果。要确认的操作，确认按钮就在卡片里 */
import { t } from '../i18n'
import { computed, ref, watch } from 'vue'
import type { ToolItem } from './fold'
import { renderMarkdown } from './markdown'

const props = defineProps<{
  item: ToolItem
  sessionId?: string
  loadScreenshot?: (sid: string, name: string) => Promise<string>
  computer?: boolean
}>()
const emit = defineEmits<{ answer: [approved: boolean, remember: boolean] }>()

const DIFF_LINES = 80
const OUTPUT_LINES = 15
const showAll = ref(false)
const manual = ref<boolean | null>(null)

const done = computed(() => !['pending', 'asking', 'running'].includes(props.item.status))
const failed = computed(() => ['error', 'denied', 'rejected', 'timeout', 'skipped', 'interrupted'].includes(props.item.status))
const isEdit = computed(() => props.item.name === 'edit_file' || props.item.name === 'write_file')
const isCommand = computed(() => props.item.name === 'run_command')
/** 派出去的助手（查资料的 explore、改代码或审查的 delegate）：结果是一份报告 */
const isExplore = computed(() => props.item.name === 'explore' || props.item.name === 'delegate')
const asking = computed(() => !!props.item.confirm && !props.item.confirm.result && props.item.status === 'asking')

/** 默认展开：改文件（看 diff）、命令（看输出）、出错、待办、助手的报告；读文件、搜索这些只看一行 */
const open = computed(
  () => manual.value ?? (isEdit.value || isCommand.value || isExplore.value || failed.value || props.item.name === 'todo_write')
)
const screenshot = ref('')
const screenshotStatus = ref('')
watch(
  () => [done.value && open.value, props.item.screenshot, props.sessionId, props.loadScreenshot] as const,
  async ([visible, name, sid, load], _previous, cleanup) => {
    let current = true
    cleanup(() => { current = false })
    screenshot.value = ''
    screenshotStatus.value = ''
    if (!visible || !name || !sid) return
    if (!load) {
      screenshotStatus.value = t('在艾拉桌面端查看截图。')
      return
    }
    screenshotStatus.value = t('正在读截图…')
    try {
      const url = await load(sid, name)
      if (current) {
        screenshot.value = url
        screenshotStatus.value = ''
      }
    } catch (err) {
      if (current) screenshotStatus.value = err instanceof Error ? err.message : t('截图读不到了。')
    }
  },
  { immediate: true }
)

const summary = computed(() => {
  const c = props.item.content
  if (!done.value) return { pending: t('等着做'), asking: t('等你确认'), running: t('正在做…') }[props.item.status] ?? ''
  if (props.item.name === 'read_file' && props.item.status === 'ok') {
    const n = c.split('\n').filter((l) => /^\s*\d+\t/.test(l)).length
    return n ? t('读了 {n} 行', { n }) : firstLine(c)
  }
  if (isEdit.value && props.item.status === 'ok') return c.match(/（\+\d+ −\d+）/)?.[0] ?? ''
  if (isCommand.value) return firstLine(c)
  if (props.item.name === 'delegate' && props.item.sub && props.item.status === 'ok') {
    const n = props.item.files?.length ?? 0
    return n
      ? t('{model} · {steps} 步 · 改了 {n} 个文件', { model: props.item.sub.model, steps: props.item.sub.steps, n })
      : t('{model} · {steps} 步', { model: props.item.sub.model, steps: props.item.sub.steps })
  }
  if (isExplore.value && props.item.sub && props.item.status === 'ok') return t('{model} · 查了 {n} 步', { model: props.item.sub.model, n: props.item.sub.steps })
  return firstLine(c)
})

const diffLines = computed(() => {
  const lines = (props.item.diff ?? '').split('\n').filter((l, i, a) => l || i < a.length - 1)
  return showAll.value ? lines : lines.slice(0, DIFF_LINES)
})
const diffMore = computed(() => Math.max(0, (props.item.diff ?? '').split('\n').length - DIFF_LINES))

const output = computed(() => {
  const body = props.item.content.split('\n').slice(1).filter((l) => l.trim())
  return showAll.value ? body : body.slice(-OUTPUT_LINES)
})
const outputMore = computed(() => Math.max(0, props.item.content.split('\n').slice(1).filter((l) => l.trim()).length - OUTPUT_LINES))

const confirmLines = computed(() => (props.item.confirm?.detail ?? '').split('\n'))
const confirmIsDiff = computed(() => props.item.confirm?.kind === 'edit' && confirmLines.value.some((l) => l.startsWith('@@')))
const rememberLabel = computed(() =>
  props.item.confirm?.kind === 'edit'
    ? t('可以，这个会话里改文件不用再问')
    : props.item.confirm?.kind === 'browser_local'
      ? t('可以，这个项目里这个端口以后都不用问')
    : props.item.confirm?.kind === 'mcp'
      ? t('可以，这个外部工具以后都不用问')
      : props.computer ? t('可以，这条命令在电脑任务里不用再问') : t('可以，这条命令在这个项目里不用再问')
)
const answered = computed(() => {
  const c = props.item.confirm
  if (!c?.result) return ''
  if (c.result === 'approved') return c.remember ? t('你同意了（以后都不用问）') : t('你同意了')
  return c.result === 'timeout' ? t('一直没等到回答') : t('你没同意')
})

function firstLine(text: string): string {
  return text.split('\n').find((l) => l.trim())?.slice(0, 160) ?? ''
}

function lineClass(line: string): string {
  if (line.startsWith('+++') || line.startsWith('---')) return 'file'
  if (line.startsWith('@@')) return 'hunk'
  if (line.startsWith('+')) return 'add'
  if (line.startsWith('-')) return 'del'
  return ''
}
</script>

<template>
  <div class="card" :class="[item.status, { asking }]">
    <button type="button" class="head" :aria-expanded="open" @click="manual = !open">
      <span class="dot" :class="item.status"></span>
      <span class="action">{{ item.action }}</span>
      <span class="summary" :class="{ bad: failed }">{{ summary }}</span>
      <span v-if="done && (item.content || item.diff || item.screenshot)" class="chev">
        {{ open ? t('收起') : item.screenshot ? t('看截图') : t('展开') }}
      </span>
    </button>

    <!-- 等你确认 -->
    <div v-if="asking && item.confirm" class="confirm">
      <div class="q">{{ t('要{action}，可以吗？', { action: item.confirm.action }) }}</div>
      <div v-if="item.confirm.reason" class="reason">{{ item.confirm.reason }}</div>
      <pre class="detail" :class="{ diff: confirmIsDiff }"><span
        v-for="(line, i) in confirmLines"
        :key="i"
        :class="confirmIsDiff ? lineClass(line) : ''"
      >{{ line || ' ' }}
</span></pre>
      <div class="buttons">
        <button class="yes" @click="emit('answer', true, false)">{{ t('可以') }} <kbd>1</kbd></button>
        <button v-if="item.confirm.canRemember" @click="emit('answer', true, true)">{{ rememberLabel }} <kbd>2</kbd></button>
        <button class="no" @click="emit('answer', false, false)">{{ t('不要，我来说怎么做') }} <kbd>Esc</kbd></button>
      </div>
    </div>
    <div v-else-if="answered" class="answered">{{ answered }}</div>
    <!-- 子 Agent 这会儿在做什么（同一步派了几个时，各自的卡片各自更新） -->
    <div v-if="!done && item.progress" class="live" role="status">
      <span class="spin" aria-hidden="true"></span>
      <b v-if="item.progress.who">{{ item.progress.who }}</b>{{ item.progress.text }}
    </div>

    <template v-if="done && open">
      <div v-if="item.screenshot" class="screenshot">
        <img v-if="screenshot" :src="screenshot" :alt="t('编程任务截取的网页')" @error="screenshot = ''; screenshotStatus = t('截图显示不了。')" />
        <div v-else role="status">{{ screenshotStatus }}</div>
      </div>
      <template v-else-if="isEdit && item.diff">
        <pre class="diff"><span v-for="(line, i) in diffLines" :key="i" :class="lineClass(line)">{{ line || ' ' }}
</span></pre>
        <button v-if="diffMore > 0 && !showAll" class="more" @click="showAll = true">{{ t('还有 {n} 行，展开全部', { n: diffMore }) }}</button>
      </template>
      <template v-else-if="isCommand">
        <div class="cmd">$ {{ String(item.args.command ?? '') }}</div>
        <pre v-if="output.length" class="output">{{ output.join('\n') }}</pre>
        <button v-if="outputMore > 0 && !showAll" class="more" @click="showAll = true">{{ t('前面还有 {n} 行，展开全部', { n: outputMore }) }}</button>
      </template>
      <template v-else-if="isExplore && item.status === 'ok'">
        <ul v-if="item.files?.length" class="files">
          <li v-for="f in item.files" :key="f"><span class="st" :class="f[0]">{{ f[0] }}</span>{{ f.slice(2) }}</li>
        </ul>
        <!-- eslint-disable-next-line vue/no-v-html：renderMarkdown 先整段转义，只插入固定的几种标签 -->
        <div class="report" v-html="renderMarkdown(item.content)"></div>
      </template>
      <ul v-else-if="item.todos" class="todos">
        <li v-for="(t, i) in item.todos" :key="i" :class="t.status">
          <span class="box">{{ t.status === 'completed' ? '☑' : t.status === 'in_progress' ? '◐' : '☐' }}</span>{{ t.content }}
        </li>
      </ul>
      <pre v-else-if="item.content && (failed || manual)" class="output">{{ item.content.split('\n').slice(0, showAll ? undefined : 40).join('\n') }}</pre>
    </template>
  </div>
</template>

<style scoped>
.card {
  margin: 8px 0;
  border: 1px solid var(--line);
  border-radius: var(--ui-r, 8px);
  background: var(--card);
  overflow: hidden;
  transition: border-color 120ms ease;
}

.card:hover {
  border-color: var(--line-strong, var(--line));
}

.card.asking {
  border-color: var(--ui-warn-line, var(--warn));
  box-shadow: 0 0 0 3px var(--ui-warn-soft, rgba(250, 204, 21, 0.12));
}

.head {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 8px 12px;
  cursor: pointer;
  user-select: none;
  font-size: 13px;
  width: 100%;
  text-align: left;
  color: inherit;
  background: transparent;
  border: 0;
}

.head:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: -2px;
}

.screenshot {
  padding: 8px 12px;
  color: var(--dim);
  border-top: 1px solid var(--line);
}

.screenshot img {
  display: block;
  max-width: 100%;
  height: auto;
}

.head:hover {
  background: rgba(255, 255, 255, 0.02);
}

.dot {
  flex: none;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--ok);
  box-shadow: 0 0 0 3px var(--ui-ok-soft, transparent);
}

.dot.pending,
.dot.running {
  background: var(--accent);
  animation: blink 1s infinite;
}

.dot.asking,
.dot.rejected,
.dot.timeout,
.dot.skipped,
.dot.interrupted {
  background: var(--warn);
}

.dot.error,
.dot.denied {
  background: var(--bad);
}

@keyframes blink {
  50% {
    opacity: 0.3;
  }
}

.action {
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 60%;
}

.summary {
  flex: 1;
  color: var(--dim);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.summary.bad {
  color: var(--bad);
}

.chev {
  color: var(--dim);
  font-size: 12px;
}

pre {
  margin: 0;
  padding: 8px 12px;
  overflow-x: auto;
  font-family: var(--ui-mono, Consolas), 'Microsoft YaHei', monospace;
  font-size: 12px;
  line-height: 1.5;
  background: var(--code);
  border-top: 1px solid var(--line);
}

pre span {
  display: block;
  white-space: pre;
}

.add {
  color: var(--ui-diff-add, #86efac);
  background: var(--ui-diff-add-bg, rgba(34, 197, 94, 0.12));
}

.del {
  color: var(--ui-diff-del, #fca5a5);
  background: var(--ui-diff-del-bg, rgba(239, 68, 68, 0.12));
}

.hunk {
  color: var(--ui-diff-hunk, #7dd3fc);
}

.file {
  color: var(--dim);
}

.output {
  color: var(--ui-code-text, #cbd5e1);
  max-height: 340px;
  white-space: pre-wrap;
  word-break: break-all;
}

.cmd {
  padding: 6px 12px;
  border-top: 1px solid var(--line);
  font-family: var(--ui-mono, Consolas, monospace);
  font-size: 12.5px;
  color: var(--ui-code-text, #e2e8f0);
  background: var(--code);
}

.more {
  display: block;
  width: 100%;
  padding: 4px;
  border: none;
  border-top: 1px solid var(--line);
  background: transparent;
  color: var(--accent);
  cursor: pointer;
}

/* 子 Agent 正在做的事：一行，跟着更新 */
.live {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 12px 8px 30px;
  color: var(--muted);
  font-size: 12px;
}

.live b {
  color: var(--ui-accent-hi, var(--text));
  font-weight: 600;
}

.live .spin {
  width: 10px;
  height: 10px;
  border: 2px solid var(--line-strong, var(--line));
  border-top-color: var(--ui-accent, #a78bfa);
  border-radius: 50%;
  animation: live-spin 0.8s linear infinite;
}

@keyframes live-spin {
  to {
    transform: rotate(360deg);
  }
}

/* 子 Agent 改了哪些文件 */
.files {
  margin: 0;
  padding: 4px 12px 0 30px;
  list-style: none;
  font-family: var(--mono, Consolas, monospace);
  font-size: 12px;
}

.files li {
  display: flex;
  gap: 8px;
  padding: 1px 0;
}

.files .st {
  width: 12px;
  font-weight: 700;
}

.files .st.A {
  color: var(--ui-ok, #4ade80);
}

.files .st.M {
  color: var(--ui-warn, #facc15);
}

.files .st.D {
  color: var(--ui-bad, #f87171);
}

.report {
  padding: 6px 12px 8px;
  border-top: 1px solid var(--line);
  font-size: 13px;
  line-height: 1.6;
  max-height: 320px;
  overflow-y: auto;
}

.report :deep(p) {
  margin: 4px 0;
}

.report :deep(ul) {
  margin: 4px 0;
  padding-left: 18px;
}

.todos {
  margin: 0;
  padding: 6px 12px 8px;
  list-style: none;
  border-top: 1px solid var(--line);
}

.todos li {
  padding: 2px 0;
}

.todos .box {
  display: inline-block;
  width: 22px;
}

.todos .completed {
  color: var(--dim);
  text-decoration: line-through;
}

.todos .in_progress {
  color: var(--accent);
  font-weight: 600;
}

.confirm {
  padding: 10px 12px 12px;
  border-top: 1px solid var(--line);
  background: rgba(250, 204, 21, 0.05);
}

.q {
  font-weight: 600;
  color: var(--warn, #fde68a);
}

.reason {
  margin-top: 2px;
  color: var(--warn);
  font-size: 12.5px;
}

.detail {
  margin: 8px 0;
  border: 1px solid var(--line);
  border-radius: 6px;
  max-height: 360px;
}

.buttons {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.buttons button {
  padding: 5px 12px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: var(--card);
  color: var(--text);
  cursor: pointer;
}

.buttons button:hover {
  border-color: var(--accent);
}

.buttons .yes {
  background: var(--accent-bg);
  border-color: var(--accent);
}

kbd {
  margin-left: 4px;
  padding: 0 4px;
  border: 1px solid var(--line);
  border-radius: 3px;
  font-size: 11px;
  color: var(--dim);
}

.answered {
  padding: 4px 12px 6px 28px;
  color: var(--dim);
  font-size: 12.5px;
}
</style>
