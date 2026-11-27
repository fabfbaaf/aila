<script setup lang="ts">
/**
 * 项目 →「看懂」：一层层看懂一个项目，每一层都能让她讲。
 * - 结构：整体架构 → 某个目录 → 文件（双击方块往里钻，上面的路径能退回去）
 * - 链路：从一个文件或者一个接口（HTTP 路径、IPC 频道）出发，一路连到最终执行的代码
 * - 出问题：贴报错，标出出错的文件和调用过来的路径，她讲为什么
 * 右边是选中的那一块：文件里的函数、函数的源码（谁调用它、它调用谁），和她的讲解。
 * 图由代码算（aila-ai/app/code/understand.py，排法在 diagram.ts），讲解是 AI 说的，和事实分开放（ExplainCard.vue）。
 */
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import type { CodeProject } from '../../../shared/settings'
import type {
  AreaView, ChainView, Diagnosis, EndpointInfo, ExplainDepth, ExplainTarget, Explanation, FileDetail, FunctionDetail
} from '../../../shared/understand'
import { t } from '../i18n'
import { createUnderstandSource } from './source'
import { edgeKind, edgeLabel, edgeTitle, layoutArea, layoutChain, neighbours, type Diagram } from './diagram'
import ExplainCard from './ExplainCard.vue'

type Mode = 'structure' | 'chain' | 'diagnose'

const source = createUnderstandSource()
const projects = ref<CodeProject[]>([])
const project = ref('')
const mode = ref<Mode>('structure')
const depth = ref<ExplainDepth>(stored('aila.understand.depth') === 'dev' ? 'dev' : 'beginner')
const error = ref('')
const loading = ref(false)
const zoom = ref(1)
const canvas = ref<HTMLElement | null>(null)

const area = ref<AreaView | null>(null)
const chainView = ref<ChainView | null>(null)
const chainFrom = ref<{ file?: string; endpoint?: string }>({})
const endpointList = ref<EndpointInfo[]>([])
const endpointQuery = ref('')

const selected = ref<string | null>(null)
const fileDetail = ref<FileDetail | null>(null)
const fnDetail = ref<FunctionDetail | null>(null)
const explanation = ref<Explanation | null>(null)
const explaining = ref(false)
let explainFor = ''

const diagText = ref('')
const diagnosis = ref<Diagnosis | null>(null)
const diagnosing = ref(false)

function stored(key: string): string | null {
  try { return localStorage.getItem(key) } catch { return null }
}
function store(key: string, value: string): void {
  try { localStorage.setItem(key, value) } catch { /* 存不了就下次再选 */ }
}
watch(depth, (d) => { store('aila.understand.depth', d); explanation.value = null })
watch(project, (p) => { if (p) store('aila.understand.project', p) })

// ---------- 图 ----------

const diagram = computed<Diagram>(() => {
  if (mode.value === 'structure') return area.value ? layoutArea(area.value) : { boxes: [], edges: [], width: 0, height: 0 }
  return chainView.value ? layoutChain(chainView.value) : { boxes: [], edges: [], width: 0, height: 0 }
})
const lit = computed(() => neighbours(diagram.value.edges, selected.value))
const hot = computed(() => new Set(mode.value === 'diagnose' ? diagnosis.value?.files ?? [] : []))
const busyEdges = computed(() => diagram.value.edges.length > 24) // 线太多：没选中时淡一点

interface BoxInfo { title: string; meta: string; kind: 'dir' | 'file' | 'outside'; tests: boolean; start: boolean }
const boxInfo = computed(() => {
  const out = new Map<string, BoxInfo>()
  if (mode.value === 'structure' && area.value) {
    for (const c of area.value.children) {
      const meta = c.kind === 'dir'
        ? t('{files} 个文件 · {lines} 行', { files: c.files, lines: c.lines })
        : t('{lines} 行 · {fns} 个函数', { lines: c.lines, fns: c.functions })
      out.set(c.id, { title: c.name, meta, kind: c.kind, tests: c.onlyTests, start: false })
    }
    for (const o of area.value.outside) out.set(o.id, { title: o.name, meta: t('外面'), kind: 'outside', tests: false, start: false })
  } else if (chainView.value) {
    for (const n of chainView.value.nodes) {
      const where = n.id.includes('/') ? n.id.slice(0, n.id.lastIndexOf('/')) : ''
      out.set(n.id, { title: n.name, meta: where, kind: 'file', tests: false, start: n.start })
    }
  }
  return out
})

function boxClass(id: string): Record<string, boolean> {
  const info = boxInfo.value.get(id)
  return {
    on: selected.value === id,
    lit: !!selected.value && lit.value.has(id) && selected.value !== id,
    dim: !!selected.value && !lit.value.has(id),
    hot: hot.value.has(id),
    outside: info?.kind === 'outside',
    tests: !!info?.tests,
    start: !!info?.start,
    dir: info?.kind === 'dir'
  }
}
function edgeClass(e: Diagram['edges'][number]): Record<string, boolean> {
  const touches = !!selected.value && (e.source === selected.value || e.target === selected.value)
  return { [edgeKind(e)]: true, lit: touches, faint: (!!selected.value && !touches) || (!selected.value && busyEdges.value), back: e.back }
}
const short = (s: string, n = 24): string => (s.length > n ? s.slice(0, n - 1) + '…' : s)

// ---------- 加载 ----------

async function guard<T>(fn: () => Promise<T>): Promise<T | undefined> {
  error.value = ''
  loading.value = true
  try {
    return await fn()
  } catch (err) {
    error.value = (err as Error).message
    return undefined
  } finally {
    loading.value = false
  }
}

function resetPanel(): void {
  selected.value = null
  fileDetail.value = null
  fnDetail.value = null
  explanation.value = null
}

async function loadProjects(): Promise<void> {
  const list = await guard(() => source.projects())
  if (!list) return
  projects.value = list
  const saved = stored('aila.understand.project')
  project.value = list.find((p) => p.id === saved)?.id ?? list[0]?.id ?? ''
}

async function openArea(path: string): Promise<void> {
  if (!project.value) return
  const view = await guard(() => source.area(project.value, path))
  if (!view) return
  mode.value = 'structure'
  area.value = view
  resetPanel()
  // 指的是一个文件：打开它所在的目录，再选中它
  if (path && view.path !== path && boxInfo.value.has(path)) await selectBox(path)
}

async function openChain(from: { file?: string; endpoint?: string }): Promise<void> {
  if (!project.value) return
  const view = await guard(() => source.chain(project.value, from.file ?? '', from.endpoint ?? ''))
  if (!view) return
  if (mode.value !== 'diagnose') mode.value = 'chain'
  chainFrom.value = from
  chainView.value = view
  resetPanel()
  if (!view.nodes.length) error.value = t('从这里连不出链路：换一个文件或者接口试试')
  else if (from.file) await selectBox(from.file)
}

async function loadEndpoints(): Promise<void> {
  if (!project.value || endpointList.value.length) return
  endpointList.value = (await guard(() => source.endpoints(project.value))) ?? []
}
const shownEndpoints = computed(() => {
  const q = endpointQuery.value.trim().toLowerCase()
  return endpointList.value.filter((e) => !q || e.name.toLowerCase().includes(q)).slice(0, 40)
})

watch(project, (p) => {
  endpointList.value = []
  chainView.value = null
  diagnosis.value = null
  if (p) void openArea('')
})
watch(mode, (m) => {
  if (m === 'chain') void loadEndpoints()
})

// ---------- 选中、往里钻 ----------

async function selectBox(id: string): Promise<void> {
  const info = boxInfo.value.get(id)
  selected.value = id
  void nextTick(() => reveal(id))
  fnDetail.value = null
  fileDetail.value = null
  explanation.value = null
  if (info?.kind === 'file') fileDetail.value = (await guard(() => source.file(project.value, id))) ?? null
}

/** 选中的方块不在眼前（外面的那一列很长）：滚到它那里 */
function reveal(id: string): void {
  const el = canvas.value?.querySelector(`[data-id="${CSS.escape(id)}"]`) as SVGGraphicsElement | null
  el?.scrollIntoView?.({ block: 'nearest', inline: 'nearest' })
}

function drill(id: string): void {
  const info = boxInfo.value.get(id)
  if (mode.value !== 'structure') {
    void openArea(id) // 链路上的文件：回到结构里看它所在的目录
    return
  }
  if (info?.kind === 'dir' || info?.kind === 'outside') void openArea(id)
  else if (info?.kind === 'file') void openChain({ file: id })
}

const crumbs = computed(() => {
  const path = area.value?.path ?? ''
  const parts = path ? path.split('/') : []
  return [{ name: t('整个项目'), path: '' }, ...parts.map((name, i) => ({ name, path: parts.slice(0, i + 1).join('/') }))]
})

async function openFunction(file: string, line: number): Promise<void> {
  const detail = await guard(() => source.fn(project.value, file, line))
  if (!detail) return
  fnDetail.value = detail
  explanation.value = null
}

// ---------- 讲解 ----------

const target = computed<ExplainTarget | null>(() => {
  if (fnDetail.value) return { level: 'function', file: fnDetail.value.file, line: fnDetail.value.start, depth: depth.value }
  if (fileDetail.value) return { level: 'file', file: fileDetail.value.file, depth: depth.value }
  if (mode.value === 'structure' && area.value) {
    const info = selected.value ? boxInfo.value.get(selected.value) : null
    const path = info?.kind === 'dir' ? selected.value! : area.value.path
    return { level: 'area', path, depth: depth.value }
  }
  if (mode.value === 'chain' && chainView.value?.nodes.length) {
    return { level: 'chain', file: chainFrom.value.file ?? '', endpoint: chainFrom.value.endpoint ?? '', depth: depth.value }
  }
  return null
})
const explainLabel = computed(() => {
  const level = target.value?.level
  if (level === 'function') return t('讲讲这个函数')
  if (level === 'file') return t('讲讲这个文件')
  if (level === 'chain') return t('讲讲这条链路')
  return selected.value ? t('讲讲这一块') : t('讲讲这一层')
})

async function explainNow(): Promise<void> {
  const goal = target.value
  if (!goal || !project.value) return
  const key = JSON.stringify(goal)
  explainFor = key
  explaining.value = true
  try {
    const res = await source.explain(project.value, goal)
    if (explainFor === key) explanation.value = res
  } catch (err) {
    if (explainFor === key) error.value = (err as Error).message
  } finally {
    if (explainFor === key) explaining.value = false
  }
}
watch(target, (now, before) => {
  if (JSON.stringify(now) !== JSON.stringify(before)) {
    explanation.value = null
    explaining.value = false
    explainFor = ''
  }
})

// ---------- 出问题 ----------

async function diagnoseNow(): Promise<void> {
  if (!project.value || !diagText.value.trim()) return
  diagnosing.value = true
  error.value = ''
  try {
    const res = await source.diagnose(project.value, diagText.value, depth.value)
    diagnosis.value = res
    if (res.found && res.files[0]) {
      const view = await source.chain(project.value, res.files[0])
      chainView.value = view
      chainFrom.value = { file: res.files[0] }
      resetPanel()
      selected.value = res.files[0]
      const first = res.frames.find((f) => f.qname)
      if (first) await openFunction(first.file, first.line)
    }
  } catch (err) {
    error.value = (err as Error).message
  } finally {
    diagnosing.value = false
  }
}

// ---------- 缩放 ----------

function zoomBy(step: number): void {
  zoom.value = Math.min(1.6, Math.max(0.4, Math.round((zoom.value + step) * 10) / 10))
}

onMounted(loadProjects)
</script>

<template>
  <div class="understand">
    <header class="bar">
      <select v-if="projects.length" v-model="project" class="pick" :aria-label="t('项目')">
        <option v-for="p in projects" :key="p.id" :value="p.id">{{ p.name }}</option>
      </select>
      <div class="modes" role="tablist">
        <button role="tab" :aria-selected="mode === 'structure'" :class="{ on: mode === 'structure' }" @click="openArea(area?.path ?? '')">{{ t('结构') }}</button>
        <button role="tab" :aria-selected="mode === 'chain'" :class="{ on: mode === 'chain' }" @click="mode = 'chain'">{{ t('链路') }}</button>
        <button role="tab" :aria-selected="mode === 'diagnose'" :class="{ on: mode === 'diagnose' }" @click="mode = 'diagnose'">{{ t('出问题了') }}</button>
      </div>
      <div class="depth" role="group" :aria-label="t('讲法')">
        <button :class="{ on: depth === 'beginner' }" :aria-pressed="depth === 'beginner'" @click="depth = 'beginner'">{{ t('零基础') }}</button>
        <button :class="{ on: depth === 'dev' }" :aria-pressed="depth === 'dev'" @click="depth = 'dev'">{{ t('开发者') }}</button>
      </div>
      <div class="zoom">
        <button :aria-label="t('缩小')" @click="zoomBy(-0.1)">−</button>
        <span>{{ Math.round(zoom * 100) }}%</span>
        <button :aria-label="t('放大')" @click="zoomBy(0.1)">+</button>
      </div>
    </header>

    <div v-if="!projects.length && !loading" class="empty">
      <p>{{ t('还没有登记项目：先把一个项目登记进来，建好索引就能一层层看懂它。') }}</p>
      <button class="primary" @click="source.openSettings()">{{ t('去登记项目') }}</button>
    </div>

    <template v-else>
      <nav v-if="mode === 'structure'" class="crumbs" :aria-label="t('位置')">
        <template v-for="(c, i) in crumbs" :key="c.path">
          <span v-if="i" class="sep">›</span>
          <button :class="{ here: i === crumbs.length - 1 }" @click="openArea(c.path)">{{ c.name }}</button>
        </template>
        <span class="tip">{{ t('单击看详情，双击往里钻；文件双击看它的链路') }}</span>
      </nav>

      <div v-else-if="mode === 'chain'" class="chain-from">
        <span v-if="chainFrom.file || chainFrom.endpoint" class="from">
          {{ t('从') }} <code>{{ chainFrom.endpoint || chainFrom.file }}</code> {{ t('出发') }}
        </span>
        <input v-model="endpointQuery" :placeholder="t('找一个接口，比如 /agent/sessions')" :aria-label="t('找接口')" @focus="loadEndpoints" />
        <div v-if="endpointQuery" class="endpoint-list">
          <button v-for="e in shownEndpoints" :key="e.kind + e.name" @click="openChain({ endpoint: e.name }); endpointQuery = ''">
            <b>{{ e.kind.toUpperCase() }}</b> {{ e.name }}
          </button>
          <p v-if="!shownEndpoints.length" class="dim">{{ t('没找到这个接口') }}</p>
        </div>
      </div>

      <div v-else class="diag-input">
        <textarea v-model="diagText" rows="4" :placeholder="t('把报错贴进来（带上堆栈，就是有文件名和行号的那几行）')" :aria-label="t('报错')"></textarea>
        <button class="primary" :disabled="diagnosing || !diagText.trim()" @click="diagnoseNow">{{ diagnosing ? t('她在找原因……') : t('找原因') }}</button>
      </div>

      <p v-if="error" class="error" role="alert" @click="error = ''">{{ error }}</p>

      <div class="body">
        <div ref="canvas" class="canvas" :aria-busy="loading">
          <p v-if="mode === 'chain' && !chainView" class="hint">{{ t('在「结构」里双击一个文件，或者在上面找一个接口，看它一路连到哪里') }}</p>
          <p v-else-if="mode === 'diagnose' && !diagnosis" class="hint">{{ t('贴进报错，她会标出出错的文件和一路调用过来的路径，再讲为什么') }}</p>
          <svg
            v-if="diagram.boxes.length"
            :width="diagram.width * zoom"
            :height="diagram.height * zoom"
            :viewBox="`0 0 ${diagram.width} ${diagram.height}`"
            role="img"
            :aria-label="t('结构图')"
            @click.self="selected = null"
          >
            <g class="edges">
              <g v-for="(e, i) in diagram.edges" :key="i" :class="edgeClass(e)">
                <title>{{ edgeTitle(e, t) }}</title>
                <path :d="e.path" />
                <path :d="e.path" class="hit" />
                <text v-if="!busyEdges || selected === e.source || selected === e.target" :x="e.lx" :y="e.ly" text-anchor="middle">{{ edgeLabel(e, t) }}</text>
              </g>
            </g>
            <g
              v-for="b in diagram.boxes"
              :key="b.id"
              :data-id="b.id"
              class="box"
              :class="boxClass(b.id)"
              tabindex="0"
              role="button"
              :aria-label="boxInfo.get(b.id)?.title ?? b.id"
              @click="selectBox(b.id)"
              @dblclick="drill(b.id)"
              @keydown.enter="drill(b.id)"
            >
              <rect :x="b.x" :y="b.y" :width="b.w" :height="b.h" rx="10" />
              <text :x="b.x + 12" :y="b.y + 24" class="title">{{ short(boxInfo.get(b.id)?.title ?? b.id) }}</text>
              <text :x="b.x + 12" :y="b.y + 43" class="meta">{{ short(boxInfo.get(b.id)?.meta ?? '', 30) }}</text>
            </g>
          </svg>
          <p v-if="mode === 'structure' && area?.hidden" class="hint">{{ t('还有 {n} 个小的没画出来', { n: area.hidden }) }}</p>
          <div class="legend">
            <span class="k http">HTTP</span><span class="k ipc">IPC</span><span class="k call">{{ t('调用') }}</span><span class="k import">{{ t('引用') }}</span>
          </div>
        </div>

        <aside class="panel">
          <!-- 出问题：出错位置 -->
          <section v-if="mode === 'diagnose' && diagnosis" class="diag-box">
            <h3>{{ t('出错的地方') }}</h3>
            <p v-if="!diagnosis.found" class="dim">{{ diagnosis.note }}</p>
            <ol v-else class="frames">
              <li v-for="(f, i) in diagnosis.frames.slice(0, 8)" :key="i">
                <button class="link" @click="openFunction(f.file, f.line)">{{ f.qname || f.file }}</button>
                <span class="dim"> {{ f.file }}:{{ f.line }}</span>
              </li>
            </ol>
            <ExplainCard v-if="diagnosis.found" :value="diagnosis" :busy="false" @say="source.say" />
          </section>

          <!-- 函数 -->
          <section v-if="fnDetail">
            <button class="link back" @click="fnDetail = null">← {{ t('回到文件') }}</button>
            <h3><code>{{ fnDetail.qname }}</code></h3>
            <p class="dim">{{ fnDetail.file }} · {{ t('第 {a}-{b} 行', { a: fnDetail.start, b: fnDetail.end }) }}</p>
            <p v-if="fnDetail.doc" class="doc">{{ fnDetail.doc }}</p>
            <pre class="code"><span v-for="(line, i) in fnDetail.lines" :key="i"><i>{{ fnDetail.start + i }}</i>{{ line }}
</span></pre>
            <p v-if="fnDetail.truncated" class="dim">{{ t('太长了，只显示前 150 行') }}</p>
            <div v-if="fnDetail.callers.length" class="chips">
              <span class="label">{{ t('谁调用它') }}</span>
              <button v-for="c in fnDetail.callers" :key="c.file + c.qname" @click="openFunction(c.file, c.start)">{{ c.qname }}</button>
            </div>
            <div v-if="fnDetail.callees.length" class="chips">
              <span class="label">{{ t('它调用了') }}</span>
              <button v-for="c in fnDetail.callees" :key="c.file + c.qname" @click="openFunction(c.file, c.start)">{{ c.qname }}</button>
            </div>
          </section>

          <!-- 文件 -->
          <section v-else-if="fileDetail">
            <h3><code>{{ fileDetail.file.split('/').pop() }}</code></h3>
            <p class="dim">{{ fileDetail.file }}</p>
            <div class="actions">
              <button class="ghost" @click="openChain({ file: fileDetail.file })">{{ t('看它的链路') }}</button>
              <button v-if="mode !== 'structure'" class="ghost" @click="openArea(fileDetail.file)">{{ t('在结构里看') }}</button>
            </div>
            <p v-if="fileDetail.usedBy.length" class="dim">{{ t('用到它的：{n} 个文件', { n: fileDetail.usedBy.length }) }}</p>
            <p v-if="fileDetail.uses.length" class="dim">{{ t('它用到的：{n} 个文件', { n: fileDetail.uses.length }) }}</p>
            <ul v-if="fileDetail.outline.length" class="outline">
              <li v-for="c in fileDetail.outline" :key="c.qname + c.start">
                <button class="link" :title="c.signature" @click="openFunction(fileDetail.file, c.start)">{{ c.qname }}</button>
                <span class="dim"> {{ c.kind }} · {{ c.start }}</span>
              </li>
            </ul>
            <p v-else class="dim">{{ t('这个文件里没认出函数（可能是配置、样式，或者还不支持的语言）') }}</p>
          </section>

          <!-- 一层 -->
          <section v-else-if="mode === 'structure' && area">
            <h3>{{ selected ? boxInfo.get(selected)?.title : area.name }}</h3>
            <template v-if="!selected">
              <p v-if="area.entries.length" class="dim">{{ t('从这里开始读：') }}</p>
              <ul v-if="area.entries.length" class="outline">
                <li v-for="f in area.entries" :key="f"><button class="link" @click="openArea(f)">{{ f }}</button></li>
              </ul>
              <p v-if="area.hubs.length" class="dim">{{ t('被用得最多的：') }}</p>
              <ul v-if="area.hubs.length" class="outline">
                <li v-for="f in area.hubs" :key="f"><button class="link" @click="openArea(f)">{{ f }}</button></li>
              </ul>
            </template>
            <template v-else-if="boxInfo.get(selected)?.kind !== 'file'">
              <p class="dim">{{ boxInfo.get(selected)?.meta }}</p>
              <button class="ghost" @click="openArea(selected)">{{ t('点进去看') }}</button>
            </template>
          </section>

          <section v-else-if="mode === 'chain' && chainView?.nodes.length">
            <h3>{{ t('这条链路') }}</h3>
            <p class="dim">{{ t('{n} 个文件，从左到右是一步步交给了谁；左边是用到起点的', { n: chainView.nodes.length }) }}</p>
          </section>

          <section v-if="target && !(mode === 'diagnose' && !fnDetail && !fileDetail)" class="explain-box">
            <button class="primary" :disabled="explaining" @click="explainNow">{{ explainLabel }}</button>
            <ExplainCard v-if="explaining || explanation" :value="explanation" :busy="explaining" @say="source.say" />
          </section>
        </aside>
      </div>
    </template>
  </div>
</template>

<style scoped>
.understand {
  display: flex;
  flex-direction: column;
  gap: 8px;
  height: 100%;
  min-height: 0;
  min-width: 0;
  overflow: hidden;
  padding: 8px 16px 12px;
  color: var(--ui-text);
}

.bar,
.crumbs,
.chain-from,
.diag-input {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.pick,
.chain-from input,
.diag-input textarea {
  padding: 5px 8px;
  border: 1px solid var(--ui-line);
  border-radius: var(--ui-r-s);
  background: var(--ui-sunken);
  color: var(--ui-text);
  font: inherit;
  font-size: 13px;
}

.chain-from input {
  flex: 1;
  min-width: 220px;
}

.diag-input textarea {
  flex: 1;
  min-width: 260px;
  font-family: var(--ui-mono);
  font-size: 12px;
  resize: vertical;
}

.modes,
.depth,
.zoom {
  display: inline-flex;
  gap: 2px;
  padding: 2px;
  border: 1px solid var(--ui-line);
  border-radius: 999px;
}

.modes button,
.depth button,
.zoom button {
  padding: 3px 12px;
  border: 0;
  border-radius: 999px;
  background: none;
  color: var(--ui-text-2);
  font-size: 12.5px;
  cursor: pointer;
}

.modes button.on,
.depth button.on {
  background: var(--ui-accent-soft);
  color: var(--ui-accent);
}

.zoom {
  align-items: center;
  margin-left: auto;
  color: var(--ui-muted);
  font-size: 12px;
}

.crumbs button {
  padding: 0;
  border: 0;
  background: none;
  color: var(--ui-accent);
  font-size: 13px;
  cursor: pointer;
}

.crumbs button.here {
  color: var(--ui-text);
  font-weight: 600;
  cursor: default;
}

.crumbs .sep {
  color: var(--ui-muted);
}

.tip {
  margin-left: auto;
  color: var(--ui-faint);
  font-size: 12px;
}

.chain-from {
  position: relative;
}

.from code {
  font-family: var(--ui-mono);
  color: var(--ui-accent);
}

.endpoint-list {
  position: absolute;
  top: 100%;
  right: 0;
  z-index: 5;
  display: flex;
  flex-direction: column;
  width: min(460px, 90%);
  max-height: 280px;
  overflow: auto;
  padding: 4px;
  border: 1px solid var(--ui-line-strong);
  border-radius: var(--ui-r-s);
  background: var(--ui-raised);
  box-shadow: var(--ui-shadow);
}

.endpoint-list button {
  padding: 5px 8px;
  border: 0;
  border-radius: var(--ui-r-s);
  background: none;
  color: var(--ui-text-2);
  font: 12.5px var(--ui-mono);
  text-align: left;
  cursor: pointer;
}

.endpoint-list button:hover {
  background: var(--ui-sunken);
}

.endpoint-list b {
  display: inline-block;
  width: 38px;
  color: var(--ui-muted);
  font-size: 10.5px;
}

.error {
  margin: 0;
  padding: 6px 10px;
  border-radius: var(--ui-r-s);
  background: var(--ui-warn-soft, rgba(217, 119, 6, 0.12));
  color: var(--ui-warn, #d97706);
  font-size: 13px;
  cursor: pointer;
}

.body {
  display: flex;
  flex: 1;
  gap: 12px;
  min-height: 0;
  min-width: 0;
}

.canvas {
  position: relative;
  flex: 1;
  min-width: 0;
  overflow: auto;
  border: 1px solid var(--ui-line);
  border-radius: var(--ui-r);
  background: var(--ui-sunken);
}

.hint {
  margin: 16px;
  color: var(--ui-muted);
  font-size: 13px;
}

.legend {
  position: sticky;
  bottom: 8px;
  left: 8px;
  display: inline-flex;
  gap: 10px;
  margin: 8px;
  padding: 3px 10px;
  border-radius: 999px;
  background: var(--ui-raised);
  font-size: 11px;
}

.k::before {
  display: inline-block;
  width: 14px;
  height: 3px;
  margin-right: 4px;
  border-radius: 2px;
  background: currentColor;
  vertical-align: middle;
  content: '';
}

.k.http, .edges .http { color: #e8b84a; }
.k.ipc, .edges .ipc { color: #4fc3d8; }
.k.call, .edges .call { color: #8a9bd8; }
.k.import, .edges .import { color: #7d8597; }

.edges path {
  fill: none;
  stroke: currentColor;
  stroke-width: 1.6;
  opacity: 0.75;
}

.edges .hit {
  stroke: transparent;
  stroke-width: 12;
  opacity: 1;
}

.edges .import path {
  stroke-dasharray: 4 4;
}

.edges .back path {
  stroke-dasharray: 2 4;
}

.edges .faint {
  opacity: 0.18;
}

.edges .lit path {
  stroke-width: 2.6;
  opacity: 1;
}

.edges text {
  fill: currentColor;
  font-size: 10.5px;
  paint-order: stroke;
  stroke: var(--ui-sunken);
  stroke-width: 3px;
}

.box {
  cursor: pointer;
}

.box rect {
  fill: var(--ui-raised);
  stroke: var(--ui-line-strong);
  stroke-width: 1.2;
}

.box.dir rect {
  fill: var(--ui-panel, var(--ui-raised));
}

.box.outside rect {
  fill: none;
  stroke-dasharray: 5 4;
}

.box.tests {
  opacity: 0.55;
}

.box.start rect {
  stroke: var(--ui-accent);
  stroke-width: 2;
}

.box.lit rect {
  stroke: var(--ui-cyan);
}

.box.on rect {
  stroke: var(--ui-accent);
  stroke-width: 2.4;
  fill: var(--ui-accent-soft);
}

.box.dim {
  opacity: 0.35;
}

.box.hot rect {
  stroke: #ef4444;
  stroke-width: 2.4;
}

.box:focus-visible rect {
  stroke: var(--ui-accent);
  stroke-width: 3;
}

.box .title {
  fill: var(--ui-text);
  font-size: 13.5px;
  font-weight: 600;
}

.box .meta {
  fill: var(--ui-muted);
  font-size: 11px;
}

.panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
  width: 360px;
  min-width: 280px;
  overflow: auto;
}

.panel section {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

/* 讲解放在最上面（文件的函数列表可能很长）；出问题时出错位置在它上面 */
.panel .diag-box {
  order: -2;
}

.panel .explain-box {
  order: -1;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--ui-line);
}

.panel h3 {
  margin: 0;
  font-size: 15px;
  overflow-wrap: anywhere;
}

.panel code {
  font-family: var(--ui-mono);
}

.dim {
  margin: 0;
  color: var(--ui-muted);
  font-size: 12px;
  overflow-wrap: anywhere;
}

.doc {
  margin: 0;
  color: var(--ui-text-2);
  font-size: 12.5px;
  white-space: pre-wrap;
}

.code {
  max-height: 340px;
  margin: 0;
  overflow: auto;
  padding: 8px;
  border-radius: var(--ui-r-s);
  background: var(--ui-sunken);
  font: 11.5px/1.55 var(--ui-mono);
  white-space: pre;
}

.code i {
  display: inline-block;
  width: 40px;
  margin-right: 10px;
  color: var(--ui-faint);
  font-style: normal;
  text-align: right;
  user-select: none;
}

.outline,
.frames {
  margin: 0;
  padding-left: 18px;
  font-size: 12.5px;
}

.outline li,
.frames li {
  margin: 2px 0;
}

.outline .dim,
.frames .dim {
  margin-left: 6px;
}

.chips {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px;
}

.chips .label {
  color: var(--ui-muted);
  font-size: 12px;
}

.chips button {
  padding: 1px 8px;
  border: 1px solid var(--ui-line);
  border-radius: 999px;
  background: var(--ui-sunken);
  color: var(--ui-text-2);
  font: 11.5px var(--ui-mono);
  cursor: pointer;
}

.actions {
  display: flex;
  gap: 6px;
}

.link {
  padding: 0;
  border: 0;
  background: none;
  color: var(--ui-accent);
  font: inherit;
  font-family: var(--ui-mono);
  font-size: 12.5px;
  text-align: left;
  cursor: pointer;
}

.link.back {
  align-self: flex-start;
  font-family: inherit;
}

.ghost,
.primary {
  align-self: flex-start;
  padding: 4px 12px;
  border: 1px solid var(--ui-line);
  border-radius: 999px;
  background: none;
  color: var(--ui-text-2);
  font-size: 12.5px;
  cursor: pointer;
}

.primary {
  border-color: var(--ui-accent-line);
  background: var(--ui-accent-soft);
  color: var(--ui-accent);
}

.primary:disabled {
  opacity: 0.6;
  cursor: default;
}

.empty {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 10px;
  padding: 24px;
  color: var(--ui-text-2);
}
</style>
