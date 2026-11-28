<script setup lang="ts">
/**
 * 交付卡：任务做完以后在这一处验收。
 *
 * - 右边栏一小块：改了几个文件、测试过没过、PR 和 CI，验收结论；点「打开交付卡」看全部
 * - 交付卡（对话框）：左边文件列表，右边这个文件的 diff；点 diff 上的某一行写意见；
 *   底下写一句总的意见，「通过」或「退回修改」。退回修改时意见由服务那边的代码拼成一段话交给她，接着在这个任务里改
 * - 卡片上的东西全是代码算的（服务的 acceptance.py），不放她自己的描述
 *
 * 数据自己拿：任务停下来（stamp 变了）就重新算一次改动和测试；有 PR 再问一次 CI（要联网，慢一点）
 */
import { computed, nextTick, ref, watch } from 'vue'
import { t } from '../i18n'
import Icon from '../theme/Icon.vue'
import type { AcceptanceEvent, DeliveryCard, LineComment } from '../../../shared/delivery'
import type { AgentSource } from './source'
import { brief, commentTarget, hasDelivery, parseDiff, putComment, sameSpot, type DiffLine } from './delivery'

const props = defineProps<{
  source: Pick<AgentSource, 'delivery' | 'accept' | 'openGithub'>
  sid: string
  /** 任务停下来时的最后一个事件号；正在做时是 null（不算，免得看到做了一半的） */
  stamp: number | null
  /** 正在做、正在合并、正在撤销……：不能验收 */
  busy: boolean
}>()

const emit = defineEmits<{
  /** 记下了验收（rejected 时服务那边已经开始新的一轮：外面接上事件流） */
  decided: [event: AcceptanceEvent]
  error: [message: string]
}>()

const card = ref<DeliveryCard | null>(null)
const ciLoading = ref(false)
const dialog = ref<HTMLDialogElement | null>(null)
const picked = ref('')
const comments = ref<LineComment[]>([])
const note = ref('')
/** 正在写意见的那一行 */
const editing = ref<{ file: string; line: number; side: 'old' | 'new'; text: string } | null>(null)
const sending = ref(false)
let request = 0

async function load(withCi: boolean): Promise<void> {
  const sid = props.sid
  const mine = ++request
  if (withCi) ciLoading.value = true
  try {
    const next = await props.source.delivery(sid, withCi)
    if (mine !== request || sid !== props.sid) return
    card.value = next
    if (!next.changes.files.some((f) => f.path === picked.value)) picked.value = next.changes.files[0]?.path ?? ''
    if (!withCi && next.pr) void load(true) // 有 PR：再问一次 CI
  } catch {
    if (mine === request && !withCi) card.value = null // 旧服务没有这个接口、或者电脑任务：不显示
  } finally {
    if (withCi && mine === request) ciLoading.value = false
  }
}

watch(
  () => [props.sid, props.stamp] as const,
  ([sid, stamp], old) => {
    if (old && old[0] !== sid) {
      card.value = null
      comments.value = []
      note.value = ''
      editing.value = null
      dialog.value?.close()
    }
    if (stamp !== null) void load(false)
  },
  { immediate: true }
)

const shown = computed(() => (hasDelivery(card.value) ? card.value : null))
const facts = computed(() => (shown.value ? brief(shown.value) : null))
const file = computed(() => shown.value?.changes.files.find((f) => f.path === picked.value) ?? null)
const lines = computed<DiffLine[]>(() => {
  if (!file.value) return []
  const all = parseDiff(file.value.diff)
  const first = all.findIndex((l) => l.kind === 'hunk')
  return first > 0 ? all.slice(first) : all // diff --git、index、---、+++ 那几行不摆（文件名左边有）
})
const countFor = (path: string): number => comments.value.filter((c) => c.file === path).length

const CI_TEXT: Record<string, string> = {
  pass: 'CI 全过了', fail: 'CI 没过', pending: 'CI 还在跑', none: 'PR 上没有检查', unknown: '问不到 CI'
}
const ciText = computed(() => {
  const ci = shown.value?.ci
  if (!shown.value?.pr) return ''
  if (!ci) return ciLoading.value ? t('正在问 CI……') : ''
  return t(CI_TEXT[ci.state] ?? ci.state)
})

async function openCard(): Promise<void> {
  if (!shown.value) return
  if (!dialog.value?.open) dialog.value?.showModal()
  if (shown.value.pr && !ciLoading.value) void load(true) // 打开时再问一次最新的 CI
}

function closeCard(): void {
  editing.value = null
  dialog.value?.close()
}

function startComment(line: DiffLine): void {
  const at = commentTarget(line)
  if (!at || !file.value || props.busy) return
  const old = comments.value.find((c) => sameSpot(c, file.value!.path, at))
  editing.value = { file: file.value.path, ...at, text: old?.text ?? '' }
  void nextTick(() => document.querySelector<HTMLTextAreaElement>('.delivery-dialog .line-edit textarea')?.focus())
}

function saveComment(): void {
  const e = editing.value
  if (!e) return
  comments.value = putComment(comments.value, { file: e.file, line: e.line, side: e.side, text: e.text })
  editing.value = null
}

function commentsAt(line: DiffLine): LineComment[] {
  const at = commentTarget(line)
  return at && file.value ? comments.value.filter((c) => sameSpot(c, file.value!.path, at)) : []
}

function isEditing(line: DiffLine): boolean {
  const at = commentTarget(line)
  return !!at && !!editing.value && !!file.value && sameSpot(editing.value, file.value.path, at)
}

async function decide(verdict: 'accepted' | 'rejected'): Promise<void> {
  if (sending.value || props.busy) return
  if (editing.value?.text.trim()) saveComment()
  const sid = props.sid
  sending.value = true
  try {
    // 交给 IPC 的要是普通对象（Vue 的响应式代理传不过去）
    const plain = verdict === 'rejected' ? comments.value.map((c) => ({ file: c.file, line: c.line, side: c.side, text: c.text })) : []
    const event = await props.source.accept(sid, verdict, note.value.trim(), plain)
    if (sid !== props.sid) return
    if (card.value) card.value = { ...card.value, acceptance: event, verdict }
    comments.value = []
    note.value = ''
    editing.value = null
    dialog.value?.close()
    emit('decided', event)
  } catch (err) {
    emit('error', (err as Error).message)
  } finally {
    sending.value = false
  }
}

function openLink(url: string): void {
  props.source.openGithub(url).catch((err: Error) => emit('error', err.message))
}

function lineNo(n: number | null): string {
  return n === null ? '' : String(n)
}
</script>

<template>
  <section v-if="shown && facts" class="delivery">
    <h3>
      {{ t('交付') }}
      <span v-if="shown.verdict === 'accepted'" class="verdict ok">{{ t('验收过了') }}</span>
      <span v-else-if="shown.verdict === 'rejected'" class="verdict bad">{{ t('退回修改了') }}</span>
    </h3>
    <ul class="facts">
      <li>
        {{ facts.files ? t('改了 {n} 个文件', { n: facts.files }) : t('没改文件') }}
        <span v-if="facts.added" class="plus">+{{ facts.added }}</span>
        <span v-if="facts.deleted" class="minus">−{{ facts.deleted }}</span>
      </li>
      <li :class="facts.tests === 'pass' ? 'ok' : facts.tests === 'fail' ? 'bad' : 'dim'">
        {{ facts.tests === 'pass' ? t('最后一次测试过了') : facts.tests === 'fail' ? t('最后一次测试没过') : t('还没跑过测试') }}
      </li>
      <li v-if="shown.pr" :class="facts.ci === 'pass' ? 'ok' : facts.ci === 'fail' ? 'bad' : 'dim'">
        PR #{{ shown.pr.number }}<template v-if="ciText"> · {{ ciText }}</template>
      </li>
    </ul>
    <button class="open" :disabled="sending" @click="openCard"><Icon name="review" :size="13" />{{ t('打开交付卡') }}</button>
  </section>

  <dialog ref="dialog" class="delivery-dialog" aria-labelledby="delivery-heading" @close="editing = null">
    <template v-if="shown && facts">
      <header>
        <h2 id="delivery-heading">{{ t('交付卡') }}</h2>
        <span v-if="shown.branch" class="chip"><Icon name="branch" :size="12" />{{ shown.branch }}</span>
        <span class="spacer"></span>
        <button class="x" :title="t('关上')" :aria-label="t('关上')" @click="closeCard"><Icon name="close" :size="14" :stroke="2" /></button>
      </header>

      <div class="summary">
        <div class="block">
          <h4>{{ t('测试') }}</h4>
          <template v-if="shown.tests">
            <p :class="shown.tests.ok ? 'ok' : 'bad'">{{ shown.tests.ok ? t('最后一次测试过了') : t('最后一次测试没过') }}</p>
            <code class="cmd">{{ shown.tests.command }}</code>
            <details v-if="shown.tests.tail">
              <summary>{{ t('看输出的最后一段') }}</summary>
              <pre>{{ shown.tests.tail }}</pre>
            </details>
          </template>
          <p v-else class="dim">{{ t('这个任务里还没跑过测试。') }}</p>
        </div>
        <div class="block">
          <h4>PR · CI</h4>
          <template v-if="shown.pr">
            <p><a href="#" @click.prevent="openLink(shown.pr.url)">{{ shown.pr.owner }}/{{ shown.pr.repo }} #{{ shown.pr.number }}</a>
              <span v-if="shown.ci?.merged" class="ok"> · {{ t('已合并') }}</span></p>
            <p v-if="ciText" :class="facts.ci === 'pass' ? 'ok' : facts.ci === 'fail' ? 'bad' : 'dim'">{{ ciText }}</p>
            <p v-if="shown.ci?.note" class="dim">{{ shown.ci.note }}</p>
            <ul v-if="shown.ci?.checks.length" class="checks">
              <li v-for="c in shown.ci.checks" :key="c.name + c.link" :class="c.state">
                <i aria-hidden="true"></i>
                <a v-if="c.link" href="#" @click.prevent="openLink(c.link)">{{ c.name }}</a><span v-else>{{ c.name }}</span>
              </li>
            </ul>
          </template>
          <p v-else class="dim">{{ t('还没开 PR（可以让她 /pr）。') }}</p>
        </div>
      </div>

      <p v-if="shown.changes.note" class="dim note">{{ shown.changes.note }}</p>

      <div v-if="shown.changes.files.length" class="changes">
        <ul class="files" role="listbox" :aria-label="t('改过的文件')">
          <li v-for="f in shown.changes.files" :key="f.path" role="option" :aria-selected="f.path === picked" :class="{ on: f.path === picked }" :title="f.path" @click="picked = f.path; editing = null">
            <span class="path">{{ f.path }}</span>
            <span class="nums">
              <span v-if="f.untracked" class="new">{{ t('新') }}</span>
              <span v-if="f.added" class="plus">+{{ f.added }}</span>
              <span v-if="f.deleted" class="minus">−{{ f.deleted }}</span>
              <span v-if="countFor(f.path)" class="count">{{ countFor(f.path) }}</span>
            </span>
          </li>
        </ul>
        <div class="diff">
          <p v-if="file && !lines.length" class="dim">{{ t('这个文件看不了 diff（二进制文件，或者没有文字上的改动）。') }}</p>
          <table v-else>
            <tbody>
              <template v-for="(l, i) in lines" :key="i">
                <tr :class="[l.kind, { pick: !!commentTarget(l) && !busy }]" @click="startComment(l)">
                  <td class="no">{{ lineNo(l.old) }}</td>
                  <td class="no">{{ lineNo(l.new) }}</td>
                  <td class="sign">{{ l.kind === 'add' ? '+' : l.kind === 'del' ? '−' : '' }}</td>
                  <td class="text">{{ l.text }}</td>
                </tr>
                <tr v-for="c in commentsAt(l)" v-show="!isEditing(l)" :key="`c${i}${c.side}`" class="comment" @click="startComment(l)">
                  <td colspan="3"></td>
                  <td>{{ c.text }}</td>
                </tr>
                <tr v-if="isEditing(l) && editing" class="line-edit">
                  <td colspan="3"></td>
                  <td>
                    <textarea v-model="editing.text" rows="2" maxlength="2000" :placeholder="t('这一行要怎么改……')" @keydown.stop @keydown.ctrl.enter="saveComment" @keydown.esc.prevent="editing = null"></textarea>
                    <div class="row">
                      <button class="small primary" @click.stop="saveComment">{{ editing.text.trim() ? t('记下这条') : t('删掉这条') }}</button>
                      <button class="small" @click.stop="editing = null">{{ t('算了') }}</button>
                    </div>
                  </td>
                </tr>
              </template>
            </tbody>
          </table>
        </div>
      </div>

      <footer>
        <p v-if="shown.acceptance" class="dim last">
          {{ shown.acceptance.verdict === 'accepted' ? t('上次：通过') : t('上次：退回修改') }}<template v-if="shown.acceptance.note">：{{ shown.acceptance.note }}</template>
        </p>
        <p class="dim hint">{{ t('点 diff 上的某一行可以写意见；退回修改时，意见会一条条交给她接着改。') }}</p>
        <textarea v-model="note" rows="2" maxlength="4000" :placeholder="t('总的意见（可以不写）')" :aria-label="t('总的意见')" @keydown.stop></textarea>
        <div class="actions">
          <span v-if="busy" class="dim">{{ t('她还在做，做完了再验收。') }}</span>
          <button class="reject" :disabled="busy || sending || (!note.trim() && !comments.length)" @click="decide('rejected')">
            {{ comments.length ? t('退回修改（{n} 条意见）', { n: comments.length }) : t('退回修改') }}
          </button>
          <button class="accept" :disabled="busy || sending" @click="decide('accepted')">{{ t('通过') }}</button>
        </div>
      </footer>
    </template>
  </dialog>
</template>

<style scoped>
.delivery h3 {
  display: flex;
  align-items: center;
  gap: 6px;
}

.verdict {
  padding: 0 6px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 500;
}

.verdict.ok {
  background: var(--ui-ok-soft);
  color: var(--ok);
}

.verdict.bad {
  background: var(--ui-warn-soft);
  color: var(--warn);
}

.facts {
  margin: 0 0 8px;
  padding: 0;
  list-style: none;
  font-size: 12.5px;
  line-height: 1.7;
}

.ok {
  color: var(--ok);
}

.bad {
  color: var(--bad);
}

.dim {
  color: var(--dim);
}

.plus {
  margin-left: 4px;
  color: var(--ok);
}

.minus {
  margin-left: 4px;
  color: var(--bad);
}

button {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px 10px;
  border: 1px solid var(--line-strong);
  border-radius: var(--ui-r-s);
  background: var(--card);
  color: var(--text);
  font: inherit;
  font-size: 12.5px;
  cursor: pointer;
}

button:disabled {
  opacity: 0.5;
  cursor: default;
}

button:not(:disabled):hover {
  border-color: var(--accent-line);
}

.delivery-dialog {
  box-sizing: border-box;
  width: min(1080px, 94vw);
  height: min(780px, 90vh);
  padding: 0;
  border: 1px solid var(--line-strong);
  border-radius: var(--ui-r-l);
  background: var(--raised);
  color: var(--text);
  box-shadow: var(--ui-shadow);
}

.delivery-dialog[open] {
  display: flex;
  flex-direction: column;
}

.delivery-dialog::backdrop {
  background: var(--ui-overlay);
  backdrop-filter: blur(2px);
}

header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 14px 18px 10px;
}

header h2 {
  margin: 0;
  font-size: 16px;
}

.chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 1px 8px;
  border-radius: 999px;
  background: var(--accent-bg);
  color: var(--text-2);
  font-size: 12px;
}

.spacer {
  flex: 1;
}

.x {
  padding: 4px;
  border-color: transparent;
  background: transparent;
}

.summary {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 12px;
  padding: 0 18px;
}

.block {
  min-width: 0;
  padding: 8px 12px;
  border: 1px solid var(--line);
  border-radius: var(--ui-r);
  background: var(--card);
  font-size: 13px;
}

.block h4 {
  margin: 0 0 4px;
  color: var(--text-2);
  font-size: 12px;
  font-weight: 600;
}

.block p {
  margin: 2px 0;
}

.cmd {
  display: block;
  overflow: hidden;
  color: var(--text-2);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

details pre {
  max-height: 160px;
  margin: 4px 0 0;
  padding: 6px 8px;
  overflow: auto;
  border-radius: 4px;
  background: var(--code);
  font-size: 11.5px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

a {
  color: var(--accent);
}

.checks {
  margin: 4px 0 0;
  padding: 0;
  list-style: none;
  font-size: 12.5px;
}

.checks li {
  display: flex;
  align-items: center;
  gap: 6px;
}

.checks i {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--dim);
}

.checks .pass i {
  background: var(--ok);
}

.checks .fail i {
  background: var(--bad);
}

.checks .pending i {
  background: var(--warn);
}

.note {
  margin: 8px 18px 0;
  font-size: 12.5px;
}

.changes {
  display: grid;
  flex: 1;
  grid-template-columns: 240px minmax(0, 1fr);
  min-height: 0;
  margin: 10px 18px 0;
  border: 1px solid var(--line);
  border-radius: var(--ui-r);
  overflow: hidden;
}

.files {
  margin: 0;
  padding: 4px 0;
  overflow: auto;
  border-right: 1px solid var(--line);
  background: var(--side);
  list-style: none;
  font-size: 12.5px;
}

.files li {
  display: flex;
  gap: 6px;
  padding: 4px 10px;
  cursor: pointer;
}

.files li.on {
  background: var(--accent-bg);
}

.files .path {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  direction: rtl;
  text-align: left;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.files .nums {
  display: flex;
  flex-shrink: 0;
  gap: 2px;
}

.new {
  padding: 0 4px;
  border-radius: 3px;
  background: var(--accent-bg);
  color: var(--accent);
  font-size: 11px;
}

.count {
  min-width: 16px;
  margin-left: 4px;
  border-radius: 8px;
  background: var(--ui-warn-soft);
  color: var(--warn);
  font-size: 11px;
  text-align: center;
}

.diff {
  min-width: 0;
  overflow: auto;
  background: var(--card);
}

.diff > p {
  padding: 12px;
}

table {
  width: 100%;
  border-collapse: collapse;
  font-family: var(--ui-mono);
  font-size: 12px;
  line-height: 1.55;
}

td {
  padding: 0 6px;
  vertical-align: top;
}

td.no {
  width: 1%;
  color: var(--dim);
  text-align: right;
  user-select: none;
  white-space: nowrap;
}

td.sign {
  width: 1%;
  padding: 0 2px;
  color: var(--dim);
  user-select: none;
}

td.text {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

tr.meta td,
tr.more td {
  color: var(--dim);
}

tr.hunk td {
  background: var(--ui-sunken);
  color: var(--ui-diff-hunk);
}

tr.add td {
  background: var(--ui-diff-add-bg);
}

tr.add td.text,
tr.add td.sign {
  color: var(--ui-diff-add);
}

tr.del td {
  background: var(--ui-diff-del-bg);
}

tr.del td.text,
tr.del td.sign {
  color: var(--ui-diff-del);
}

tr.pick {
  cursor: pointer;
}

tr.pick:hover td {
  outline: 1px solid var(--accent-line);
  outline-offset: -1px;
}

tr.comment td,
tr.line-edit td {
  padding-top: 4px;
  padding-bottom: 4px;
  background: var(--ui-warn-soft);
  font-family: var(--ui-font);
}

tr.comment td:last-child {
  border-left: 3px solid var(--warn);
  cursor: pointer;
}

textarea {
  box-sizing: border-box;
  width: 100%;
  padding: 6px 8px;
  border: 1px solid var(--line-strong);
  border-radius: 6px;
  background: var(--bg);
  color: var(--text);
  font: inherit;
  font-size: 13px;
  resize: vertical;
}

.row {
  display: flex;
  gap: 6px;
  margin-top: 4px;
}

.small {
  padding: 2px 8px;
  font-size: 12px;
}

.primary,
.accept {
  border-color: var(--ui-ok-line);
  background: var(--ui-ok-soft);
  color: var(--ok);
}

.primary {
  border-color: var(--accent-line);
  background: var(--accent-bg);
  color: var(--accent);
}

.reject {
  border-color: var(--ui-warn-line);
  background: var(--ui-warn-soft);
  color: var(--warn);
}

footer {
  padding: 10px 18px 16px;
}

footer p {
  margin: 0 0 6px;
  font-size: 12.5px;
}

.actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 8px;
}

.actions .dim {
  margin-right: auto;
  font-size: 12.5px;
}

.open {
  width: 100%;
  justify-content: center;
}
</style>
