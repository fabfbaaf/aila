<script setup lang="ts">
/** 一个工具调用：一行「做什么」+ 结果。要确认的操作，确认按钮就在卡片里 */
import { computed, ref } from 'vue'
import type { ToolItem } from './fold'

const props = defineProps<{ item: ToolItem }>()
const emit = defineEmits<{ answer: [approved: boolean, remember: boolean] }>()

const DIFF_LINES = 80
const OUTPUT_LINES = 15
const showAll = ref(false)
const manual = ref<boolean | null>(null)

const done = computed(() => !['pending', 'asking', 'running'].includes(props.item.status))
const failed = computed(() => ['error', 'denied', 'rejected', 'timeout', 'skipped', 'interrupted'].includes(props.item.status))
const isEdit = computed(() => props.item.name === 'edit_file' || props.item.name === 'write_file')
const isCommand = computed(() => props.item.name === 'run_command')
const asking = computed(() => !!props.item.confirm && !props.item.confirm.result && props.item.status === 'asking')

/** 默认展开：改文件（看 diff）、命令（看输出）、出错、待办；读文件、搜索这些只看一行 */
const open = computed(() => manual.value ?? (isEdit.value || isCommand.value || failed.value || props.item.name === 'todo_write'))

const summary = computed(() => {
  const c = props.item.content
  if (!done.value) return { pending: '等着做', asking: '等你确认', running: '正在做…' }[props.item.status] ?? ''
  if (props.item.name === 'read_file' && props.item.status === 'ok') {
    const n = c.split('\n').filter((l) => /^\s*\d+\t/.test(l)).length
    return n ? `读了 ${n} 行` : firstLine(c)
  }
  if (isEdit.value && props.item.status === 'ok') return c.match(/（\+\d+ −\d+）/)?.[0] ?? ''
  if (isCommand.value) return firstLine(c)
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
  props.item.confirm?.kind === 'edit' ? '可以，这个会话里改文件不用再问' : '可以，这条命令在这个项目里不用再问'
)
const answered = computed(() => {
  const c = props.item.confirm
  if (!c?.result) return ''
  if (c.result === 'approved') return c.remember ? '你同意了（以后都不用问）' : '你同意了'
  return c.result === 'timeout' ? '一直没等到回答' : '你没同意'
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
    <div class="head" @click="manual = !open">
      <span class="dot" :class="item.status"></span>
      <span class="action">{{ item.action }}</span>
      <span class="summary" :class="{ bad: failed }">{{ summary }}</span>
      <span v-if="done && (item.content || item.diff)" class="chev">{{ open ? '收起' : '展开' }}</span>
    </div>

    <!-- 等你确认 -->
    <div v-if="asking && item.confirm" class="confirm">
      <div class="q">要{{ item.confirm.action }}，可以吗？</div>
      <div v-if="item.confirm.reason" class="reason">{{ item.confirm.reason }}</div>
      <pre class="detail" :class="{ diff: confirmIsDiff }"><span
        v-for="(line, i) in confirmLines"
        :key="i"
        :class="confirmIsDiff ? lineClass(line) : ''"
      >{{ line || ' ' }}
</span></pre>
      <div class="buttons">
        <button class="yes" @click="emit('answer', true, false)">可以 <kbd>1</kbd></button>
        <button v-if="item.confirm.canRemember" @click="emit('answer', true, true)">{{ rememberLabel }} <kbd>2</kbd></button>
        <button class="no" @click="emit('answer', false, false)">不要，我来说怎么做 <kbd>Esc</kbd></button>
      </div>
    </div>
    <div v-else-if="answered" class="answered">{{ answered }}</div>

    <template v-if="done && open">
      <template v-if="isEdit && item.diff">
        <pre class="diff"><span v-for="(line, i) in diffLines" :key="i" :class="lineClass(line)">{{ line || ' ' }}
</span></pre>
        <button v-if="diffMore > 0 && !showAll" class="more" @click="showAll = true">还有 {{ diffMore }} 行，展开全部</button>
      </template>
      <template v-else-if="isCommand">
        <div class="cmd">$ {{ String(item.args.command ?? '') }}</div>
        <pre v-if="output.length" class="output">{{ output.join('\n') }}</pre>
        <button v-if="outputMore > 0 && !showAll" class="more" @click="showAll = true">前面还有 {{ outputMore }} 行，展开全部</button>
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
  margin: 6px 0;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--card);
  overflow: hidden;
}

.card.asking {
  border-color: var(--warn);
  box-shadow: 0 0 0 1px rgba(250, 204, 21, 0.25);
}

.head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 12px;
  cursor: pointer;
  user-select: none;
}

.dot {
  flex: none;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--ok);
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
  font-family: Consolas, 'Cascadia Mono', 'Microsoft YaHei', monospace;
  font-size: 12.5px;
  line-height: 1.5;
  background: var(--code);
  border-top: 1px solid var(--line);
}

pre span {
  display: block;
  white-space: pre;
}

.add {
  color: #86efac;
  background: rgba(34, 197, 94, 0.12);
}

.del {
  color: #fca5a5;
  background: rgba(239, 68, 68, 0.12);
}

.hunk {
  color: #7dd3fc;
}

.file {
  color: var(--dim);
}

.output {
  color: #cbd5e1;
  max-height: 340px;
  white-space: pre-wrap;
  word-break: break-all;
}

.cmd {
  padding: 6px 12px;
  border-top: 1px solid var(--line);
  font-family: Consolas, monospace;
  font-size: 12.5px;
  color: #e2e8f0;
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
  color: #fde68a;
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
