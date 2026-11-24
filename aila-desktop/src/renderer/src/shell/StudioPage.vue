<script setup lang="ts">
/**
 * 作品页：她自己做的小作品，和她的日记。
 *
 * - 作品：每件一张卡片（在做 / 等你点头 / 做好了 / 没做成），点开看她写的说明、文件，网页作品能直接打开玩
 *   （单独的沙盒窗口，不能联网），也能看她是怎么一步步做出来的（打开任务页里那个会话）
 * - 「现在就让她做一个」：可以给个点子，不给就她自己挑
 * - 日记：每天晚上一篇
 * 什么时候自己做、每件花多少钱，在设置 → 桌面里改。
 */
import { t, localized, dateText } from '../i18n'
import { computed, onActivated, onDeactivated, onMounted, ref, watch } from 'vue'
import { WORK_STATUS_TEXT as WORK_STATUS_TEXT_RAW, type DiaryEntry, type Work, type WorkDetail } from '../../../shared/studio'
const WORK_STATUS_TEXT = localized(WORK_STATUS_TEXT_RAW)
import { renderMarkdown } from '../agent/markdown'
import { weekOf, type ReviewEntry } from '../../../shared/review'
import { createStudioSource } from './studioSource'

const props = defineProps<{ work?: string }>()
const emit = defineEmits<{ go: [route: { page: 'settings'; section: 'desktop' }] }>()

const source = createStudioSource()
const tab = ref<'works' | 'diary' | 'review'>('works')
const reviews = ref<ReviewEntry[]>([])
const writing = ref(false)
const copied = ref('')
const thisWeek = weekOf(new Date()).week
const works = ref<Work[]>([])
const diary = ref<DiaryEntry[]>([])
const known = ref<{ firstSeen?: string; days: number } | null>(null)
const selected = ref<WorkDetail | null>(null)
const error = ref('')
const busy = ref(false)
const asking = ref(false)
const ideaTitle = ref('')
const ideaText = ref('')
const ideaKind = ref<'web' | 'script'>('web')
const confirmDelete = ref(false)
let timer = 0

const making = computed(() => works.value.find((w) => w.status === 'making' || w.status === 'waiting') ?? null)

function dateOf(seconds: number): string {
  const d = new Date(seconds * 1000)
  return dateText(d, true)
}

async function load(): Promise<void> {
  try {
    works.value = await source.works()
    error.value = ''
  } catch (err) {
    error.value = (err as Error).message
  }
  if (selected.value) {
    const fresh = works.value.find((w) => w.id === selected.value!.id)
    if (fresh && fresh.status !== selected.value.status) await select(fresh.id)
  }
}

async function loadDiary(): Promise<void> {
  try {
    diary.value = await source.diary()
  } catch (err) {
    error.value = (err as Error).message
  }
}

async function select(id: string): Promise<void> {
  confirmDelete.value = false
  try {
    selected.value = await source.work(id)
  } catch (err) {
    error.value = (err as Error).message
  }
}

async function makeNow(): Promise<void> {
  busy.value = true
  error.value = ''
  try {
    const idea = ideaTitle.value.trim() && ideaText.value.trim()
      ? { title: ideaTitle.value.trim(), idea: ideaText.value.trim(), kind: ideaKind.value }
      : undefined
    const w = await source.now(idea)
    asking.value = false
    ideaTitle.value = ideaText.value = ''
    await load()
    await select(w.id)
  } catch (err) {
    error.value = (err as Error).message
  } finally {
    busy.value = false
  }
}

async function open(w: WorkDetail): Promise<void> {
  try {
    await source.open(w.id, w.entry)
  } catch (err) {
    error.value = (err as Error).message
  }
}

async function remove(w: WorkDetail): Promise<void> {
  try {
    await source.remove(w.id)
    selected.value = null
    confirmDelete.value = false
    await load()
  } catch (err) {
    error.value = (err as Error).message
  }
}

watch(tab, (t) => {
  if (t === 'diary') void loadDiary()
  if (t === 'review') void loadReviews()
})

// ---------- 每周回顾（app/review.py） ----------

async function loadReviews(): Promise<void> {
  try {
    reviews.value = await source.reviews()
  } catch (err) {
    error.value = (err as Error).message
  }
}

async function writeReview(force: boolean): Promise<void> {
  if (!source.reviewNow) return
  writing.value = true
  error.value = ''
  try {
    await source.reviewNow(force)
    await loadReviews()
  } catch (err) {
    error.value = (err as Error).message
  } finally {
    writing.value = false
  }
}

async function copyReview(r: ReviewEntry): Promise<void> {
  await navigator.clipboard.writeText(r.markdown)
  copied.value = r.week
  window.setTimeout(() => (copied.value = ''), 2000)
}

watch(
  () => props.work,
  (id) => {
    if (id) {
      tab.value = 'works'
      void select(id)
    }
  }
)

onMounted(async () => {
  await load()
  known.value = await source.known().catch(() => null)
  if (props.work) await select(props.work)
})

// 在看这一页、她又在做的时候勤快点刷：做好了马上看得到
onActivated(() => {
  void load()
  timer = window.setInterval(() => {
    if (making.value) void load()
  }, 5000)
})
onDeactivated(() => window.clearInterval(timer))
</script>

<template>
  <div class="studio">
    <header class="bar">
      <b>{{ t('她的作品') }}</b>
      <nav class="tabs" role="tablist">
        <button role="tab" :aria-selected="tab === 'works'" :class="{ on: tab === 'works' }" @click="tab = 'works'">{{ t('作品') }}</button>
        <button role="tab" :aria-selected="tab === 'diary'" :class="{ on: tab === 'diary' }" @click="tab = 'diary'">{{ t('日记') }}</button>
        <button role="tab" :aria-selected="tab === 'review'" :class="{ on: tab === 'review' }" @click="tab = 'review'">{{ t('回顾') }}</button>
      </nav>
      <span v-if="known && known.days" class="known">{{ t('认识第 {n} 天', { n: known.days }) }}</span>
      <span class="spacer"></span>
      <button v-if="tab === 'works'" class="primary" :disabled="!!making || busy" @click="asking = !asking">
        {{ making ? t('她在做「{title}」', { title: making.title }) : t('现在就让她做一个') }}
      </button>
    </header>

    <div v-if="error" class="error" @click="error = ''">{{ error }}</div>

    <template v-if="tab === 'works'">
      <form v-if="asking" class="ask" @submit.prevent="makeNow">
        <p>{{ t('给她出个题，或者什么都不写，让她自己挑。她只在自己的文件夹里做，你的项目一个字都不碰。') }}</p>
        <div class="row">
          <input v-model="ideaTitle" maxlength="40" :placeholder="t('名字（比如：番茄小钟）')" />
          <select v-model="ideaKind" :aria-label="t('做成什么')">
            <option value="web">{{ t('网页') }}</option>
            <option value="script">{{ t('Python 脚本') }}</option>
          </select>
        </div>
        <textarea v-model="ideaText" maxlength="300" rows="2" :placeholder="t('做什么、怎么玩（不写就她自己挑）')"></textarea>
        <div class="row end">
          <button type="button" class="ghost" @click="asking = false">{{ t('算了') }}</button>
          <button type="submit" class="primary" :disabled="busy">{{ busy ? t('她在想做什么……') : t('去做吧') }}</button>
        </div>
      </form>

      <div class="layout" :class="{ open: !!selected }">
        <div class="grid">
          <p v-if="!works.length" class="empty">
            {{ t('她还没做过作品。点右上角「现在就让她做一个」，或者在') }}
            <a href="#" @click.prevent="emit('go', { page: 'settings', section: 'desktop' })">{{ t('设置 → 桌面') }}</a>
            {{ t('里让她隔几天自己做一个——做好了会在桌面上告诉你。') }}
          </p>
          <article
            v-for="w in works"
            :key="w.id"
            class="card"
            :class="[w.status, { on: selected?.id === w.id }]"
            @click="select(w.id)"
          >
            <div class="title">{{ w.title }}</div>
            <div class="meta">
              <span class="chip" :class="w.status">{{ WORK_STATUS_TEXT[w.status] }}</span>
              <span>{{ dateOf(w.created) }}</span>
              <span v-if="w.occasion" class="occasion">{{ w.occasion }}</span>
            </div>
            <p class="idea">{{ w.idea }}</p>
          </article>
        </div>

        <aside v-if="selected" class="detail">
          <div class="head">
            <h2>{{ selected.title }}</h2>
            <button class="x" :title="t('关掉')" @click="selected = null">×</button>
          </div>
          <div class="meta">
            <span class="chip" :class="selected.status">{{ WORK_STATUS_TEXT[selected.status] }}</span>
            <span>{{ dateOf(selected.created) }}</span>
            <span>{{ selected.kind === 'web' ? t('网页') : t('Python 脚本') }}</span>
          </div>
          <p v-if="selected.why" class="why">「{{ selected.why }}」</p>
          <p v-if="selected.status === 'failed'" class="reason">{{ t('没做成：{why}', { why: selected.reason ?? '' }) }}</p>
          <p v-if="selected.status === 'waiting'" class="reason">{{ t('她要做的一步需要你点头，去任务页看看。') }}</p>
          <div class="actions">
            <button v-if="selected.entry.endsWith('.html')" class="primary" @click="open(selected)">{{ t('打开玩玩') }}</button>
            <button v-if="source.reveal && selected.files.length" @click="source.reveal!(selected.id)">{{ t('打开文件夹') }}</button>
            <button v-if="source.openSession" @click="source.openSession!(selected.session)">{{ t('看她怎么做的') }}</button>
            <button v-if="!confirmDelete" class="ghost danger" @click="confirmDelete = true">{{ t('删掉') }}</button>
            <template v-else>
              <span class="confirm">{{ t('删掉这件作品和它的文件？找不回来。') }}</span>
              <button class="danger" @click="remove(selected)">{{ t('删掉') }}</button>
              <button class="ghost" @click="confirmDelete = false">{{ t('不删') }}</button>
            </template>
          </div>
          <!-- eslint-disable-next-line vue/no-v-html：renderMarkdown 先整段转义，只插入固定的几种标签 -->
          <div v-if="selected.readme" class="readme md" v-html="renderMarkdown(selected.readme)"></div>
          <div v-if="selected.files.length" class="files">
            <div class="files-title">{{ t('文件') }}</div>
            <div v-for="f in selected.files" :key="f.path" class="file">
              {{ f.path }}<span>{{ (f.size / 1024).toFixed(1) }} KB</span>
            </div>
          </div>
        </aside>
      </div>
    </template>

    <section v-else-if="tab === 'review'" class="diary">
      <div class="review-bar">
        <span class="hint">{{ t('每周五晚上六点以后她写一份这周的回顾：做了什么、还没做完、下周约好的事，改两句就能当周报（设置 → 桌面里可以关掉）。') }}</span>
        <button v-if="source.reviewNow" class="primary" :disabled="writing" @click="writeReview(reviews.some((r) => r.week === thisWeek))">
          {{ writing ? t('在写……') : reviews.some((r) => r.week === thisWeek) ? t('重写这周的') : t('现在写这周的') }}
        </button>
      </div>
      <p v-if="!reviews.length" class="empty">{{ t('还没有回顾。周五晚上她会写第一份，也可以点上面的按钮现在就写。') }}</p>
      <article v-for="r in reviews" :key="r.week" class="entry">
        <div class="day">
          {{ r.start }} ～ {{ r.end }}
          <button class="copy" @click="copyReview(r)">{{ copied === r.week ? t('复制好了') : t('复制') }}</button>
        </div>
        <!-- eslint-disable-next-line vue/no-v-html -- renderMarkdown 先整段转义，只插固定标签 -->
        <div class="md" v-html="renderMarkdown(r.markdown)" />
      </article>
    </section>

    <section v-else class="diary">
      <p v-if="!diary.length" class="empty">{{ t('还没有日记。她每天晚上十点以后写一篇（设置 → 桌面里可以关掉）。') }}</p>
      <article v-for="d in diary" :key="d.day" class="entry">
        <div class="day">{{ d.day }}</div>
        <p>{{ d.text }}</p>
        <div v-if="d.works.length" class="tags">
          <span v-for="t in d.works" :key="t">「{{ t }}」</span>
        </div>
      </article>
    </section>
  </div>
</template>

<style scoped>
.studio {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-width: 0;
  color: var(--ui-text);
}

.bar {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 16px 22px 12px;
  border-bottom: 1px solid var(--ui-line);
}

.bar b {
  font-size: 17px;
}

.tabs {
  display: flex;
  gap: 2px;
  padding: 2px;
  border: 1px solid var(--ui-line);
  border-radius: var(--ui-r-s);
}

.tabs button {
  padding: 4px 12px;
  border: none;
  border-radius: var(--ui-r-xs);
  background: transparent;
  color: var(--ui-muted);
  font: inherit;
  cursor: pointer;
}

.tabs button.on {
  background: var(--ui-raised);
  color: var(--ui-text);
}

.known {
  color: var(--ui-muted);
  font-size: 12px;
}

.spacer {
  flex: 1;
}

button {
  padding: 6px 12px;
  border: 1px solid var(--ui-line-strong);
  border-radius: var(--ui-r-s);
  background: var(--ui-raised);
  color: var(--ui-text);
  font: inherit;
  font-size: 13px;
  cursor: pointer;
}

button.primary {
  border-color: var(--ui-accent-line);
  background: var(--ui-accent-soft);
  color: var(--ui-accent-hi);
}

button.ghost {
  background: transparent;
}

button.danger {
  color: var(--ui-bad-text);
}

button:disabled {
  opacity: 0.6;
  cursor: default;
}

.error {
  margin: 10px 22px 0;
  padding: 8px 12px;
  border: 1px solid var(--ui-bad-line);
  border-radius: var(--ui-r-s);
  background: var(--ui-bad-soft);
  color: var(--ui-bad-text);
  cursor: pointer;
}

.ask {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin: 12px 22px 0;
  padding: 12px 14px;
  border: 1px solid var(--ui-line-strong);
  border-radius: var(--ui-r);
  background: var(--ui-surface);
}

.ask p {
  margin: 0;
  color: var(--ui-muted);
  font-size: 13px;
}

.ask .row {
  display: flex;
  gap: 8px;
}

.ask .row.end {
  justify-content: flex-end;
}

.ask input,
.ask select,
.ask textarea {
  padding: 7px 10px;
  border: 1px solid var(--ui-line);
  border-radius: var(--ui-r-s);
  background: var(--ui-field);
  color: var(--ui-text);
  font: inherit;
  font-size: 13px;
}

.ask input {
  flex: 1;
}

.ask textarea {
  resize: vertical;
}

.layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  flex: 1;
  min-height: 0;
}

.layout.open {
  grid-template-columns: minmax(0, 1fr) minmax(320px, 440px);
}

.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  align-content: start;
  gap: 12px;
  padding: 16px 22px;
  overflow: auto;
}

.empty {
  grid-column: 1 / -1;
  color: var(--ui-muted);
  line-height: 1.7;
}

.empty a {
  color: var(--ui-accent);
}

.card {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 12px 14px;
  border: 1px solid var(--ui-line);
  border-radius: var(--ui-r);
  background: var(--ui-surface);
  cursor: pointer;
}

.card:hover,
.card.on {
  border-color: var(--ui-accent-line);
}

.card.failed {
  opacity: 0.7;
}

.title {
  font-weight: 600;
}

.meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  color: var(--ui-muted);
  font-size: 12px;
}

.chip {
  padding: 0 7px;
  border-radius: 999px;
  border: 1px solid var(--ui-line);
}

.chip.done {
  border-color: var(--ui-ok-line);
  color: var(--ui-ok);
}

.chip.making {
  border-color: var(--ui-cyan-line);
  color: var(--ui-cyan);
}

.chip.waiting {
  border-color: var(--ui-warn-line);
  color: var(--ui-warn);
}

.chip.failed {
  color: var(--ui-bad-text);
}

.occasion {
  color: var(--ui-warn);
}

.idea {
  margin: 0;
  color: var(--ui-text-2);
  font-size: 13px;
  line-height: 1.6;
}

.detail {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 16px 18px;
  overflow: auto;
  border-left: 1px solid var(--ui-line);
  background: var(--ui-panel);
}

.detail .head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.detail h2 {
  margin: 0;
  font-size: 18px;
}

.x {
  padding: 0 8px;
  border: none;
  background: transparent;
  font-size: 18px;
}

.why {
  margin: 0;
  color: var(--ui-text-2);
  font-style: italic;
}

.reason {
  margin: 0;
  color: var(--ui-warn);
  font-size: 13px;
}

.actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.confirm {
  color: var(--ui-bad-text);
  font-size: 12px;
}

.readme {
  padding: 10px 12px;
  border: 1px solid var(--ui-line);
  border-radius: var(--ui-r-s);
  background: var(--ui-surface);
  line-height: 1.7;
  font-size: 13px;
}

.files-title {
  margin-bottom: 4px;
  color: var(--ui-muted);
  font-size: 12px;
}

.file {
  display: flex;
  justify-content: space-between;
  padding: 3px 0;
  font-family: var(--ui-mono);
  font-size: 12px;
}

.file span {
  color: var(--ui-muted);
}

.diary {
  display: flex;
  flex-direction: column;
  gap: 14px;
  max-width: 760px;
  padding: 18px 22px;
  overflow: auto;
}

.entry {
  padding: 14px 16px;
  border: 1px solid var(--ui-line);
  border-radius: var(--ui-r);
  background: var(--ui-surface);
}

.entry .day {
  margin-bottom: 6px;
  color: var(--ui-accent);
  font-weight: 600;
}

.entry p {
  margin: 0;
  line-height: 1.85;
  white-space: pre-wrap;
}

/* 每周回顾 */
.review-bar {
  display: flex;
  gap: 12px;
  align-items: center;
}

.review-bar .hint {
  flex: 1;
  color: var(--ui-muted);
  font-size: 12.5px;
}

.entry .day {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.copy {
  padding: 2px 10px;
  border: 1px solid var(--ui-field);
  border-radius: var(--ui-r);
  background: transparent;
  color: var(--ui-muted);
  font: inherit;
  font-size: 12px;
  font-weight: 400;
  cursor: pointer;
}

.md {
  line-height: 1.75;
}

.md :deep(h4) {
  margin: 12px 0 4px;
  font-size: 14.5px;
}

.md :deep(h4:first-child) {
  margin-top: 0;
}

.md :deep(ul) {
  margin: 4px 0;
  padding-left: 22px;
}

.md :deep(p) {
  margin: 6px 0;
}

.tags {
  margin-top: 8px;
  color: var(--ui-muted);
  font-size: 12px;
}
</style>
