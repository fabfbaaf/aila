<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import type { CodeProject } from '../../../shared/settings'
import type { ChunkSource, CodeGraph, GraphNode, LocatedError, OutlineItem, PendingFix, WorkbenchFocus } from '../../../shared/workbench'
import { diffLines } from '../components/bubble'
import { lines } from '../persona/lines'
import GestureLayer from '../gestures/GestureLayer.vue'
import type { GestureEvent } from '../gestures/GestureEngine'
import {
  CHAIN_COLOR,
  ERROR_COLOR,
  HTTP_COLOR,
  IPC_COLOR,
  TOUCH_COLOR,
  buildModules,
  crossLinks,
  groupColors,
  highlightFrom,
  kindOf,
  searchNodes,
  starOf,
  withoutTests,
  type ModuleInfo
} from './layout'
import type { AgentActivity, AgentSessionSummary } from '../../../shared/agent'
import { buildReplay, editedUpTo, sessionsIn, type ReplayStep } from './replay'
import { createSource } from './source'
import { StarMap } from './StarMap'

const emit = defineEmits<{ close: [] }>()

const source = createSource()
/** 主进程推过来的消息：离开地图时退订（地图只在看它的时候才挂着，免得在后台一直画） */
const unsubscribe: (() => void)[] = []
const canvas = ref<HTMLDivElement>()
let map: StarMap | null = null

const projects = ref<CodeProject[]>([])
const projectId = ref('')
const graph = shallowRef<CodeGraph | null>(null)
const status = ref('正在读取项目……')
const node = ref<GraphNode | null>(null)
const outline = ref<OutlineItem[]>([])
const code = ref<ChunkSource | null>(null)
const query = ref('')
const tip = ref<{ name: string; lines: number; x: number; y: number } | null>(null)
const located = ref<LocatedError | null>(null)
const errorText = ref('')
const notice = ref('')
/** 手势开着（摄像头开着）。不记住：每次打开星图都要自己点，免得一打开窗口摄像头就亮了 */
const gestures = ref(false)
/** 捏住的星球：往面前拉就展开它的代码 */
const grabbed = ref<GraphNode | null>(null)
const fix = ref<PendingFix | null>(null)
const toast = ref('')
/** 工作窗口里她正在碰的文件（底部一行） */
const activity = ref('')
let activityTimer = 0

/** 她在哪个文件上干活：是这个项目里的文件就在星图上标出来 */
function onActivity(a: AgentActivity): void {
  const root = projects.value.find((p) => p.id === projectId.value)?.root
  const id = root ? starOf(root, a.cwd, a.file) : null
  if (!id || !map?.touch(id, a.kind)) return
  activity.value = `她正在${a.kind === 'read' ? '读' : '改'} ${id}`
  clearTimeout(activityTimer)
  activityTimer = window.setTimeout(() => (activity.value = ''), 6000)
}
let toastTimer = 0

/** 隐藏测试文件（默认只是调暗） */
const hideTests = ref(false)
/** 星图上画的：隐藏测试文件时去掉它们 */
const shown = computed(() => (graph.value && hideTests.value ? withoutTests(graph.value) : graph.value))
const counts = computed(() => {
  const g = shown.value
  if (!g) return null
  const cross = g.links.filter((l) => kindOf(l) !== 'call')
  return {
    files: g.nodes.length,
    modules: buildModules(g).modules.length,
    calls: g.links.length - cross.length,
    cross: cross.length,
    tests: graph.value?.nodes.filter((n) => n.test).length ?? 0
  }
})
const moduleTip = ref<{ m: ModuleInfo; x: number; y: number } | null>(null)
/** 选中文件的跨进程光线：它请求了谁、谁请求了它 */
const cross = computed(() => (node.value && graph.value ? crossLinks(graph.value, node.value.id) : null))
const results = computed(() => (shown.value ? searchNodes(shown.value.nodes, query.value) : []))
const legend = computed(() => (graph.value ? [...groupColors(graph.value.nodes).entries()] : []))
const primary = computed(() => (located.value ? highlightFrom(located.value, graph.value ?? { nodes: [], links: [] }).primary : null))
const kinds: Record<string, string> = { function: '函数', method: '方法', class: '类', template: '界面模板' }
/** 修改的说明（项目、改了什么、怎么测）和 diff 分开显示 */
const fixLines = computed(() => {
  const lines = diffLines(fix.value?.detail ?? '')
  const start = lines.findIndex((l) => l.kind === 'file')
  return start < 0 ? { info: lines, diff: [] } : { info: lines.slice(0, start), diff: lines.slice(start) }
})

// ---------- 读数据 ----------

async function loadProjects(): Promise<void> {
  try {
    projects.value = await source.projects()
  } catch (err) {
    status.value = `连不上 AI 服务：${(err as Error).message}`
    return
  }
  if (!projects.value.length) {
    status.value = ''
    return
  }
  if (!projectId.value) projectId.value = projects.value[0].id
}

async function loadGraph(): Promise<void> {
  if (!projectId.value || !map) return
  status.value = '正在画星图……'
  node.value = null
  code.value = null
  try {
    graph.value = await source.graph(projectId.value)
  } catch (err) {
    status.value = `读不了这个项目：${(err as Error).message}`
    return
  }
  map.setGraph(shown.value!)
  status.value = ''
  if (located.value?.project === projectId.value) showError(located.value)
}

watch(projectId, () => void loadGraph())
watch(hideTests, () => {
  if (!map || !shown.value) return
  map.setGraph(shown.value)
  if (node.value && !shown.value.nodes.some((n) => n.id === node.value!.id)) node.value = null
  else if (node.value) map.select(node.value.id, false)
  if (located.value?.project === projectId.value) showError(located.value)
})

/** 面板里点一个跨进程的文件：飞过去看它 */
function jump(id: string): void {
  const n = graph.value?.nodes.find((x) => x.id === id)
  if (!n) return
  if (n.test && hideTests.value) hideTests.value = false
  pick(n)
}

// ---------- 选星球、看代码 ----------

async function selectNode(n: GraphNode | null, line?: number): Promise<void> {
  node.value = n
  code.value = null
  outline.value = []
  if (!n) return
  outline.value = await source.outline(projectId.value, n.id).catch(() => [])
  const at = line ?? (primary.value?.file === n.id ? primary.value.line : outline.value[0]?.start)
  if (at) code.value = await source.chunk(projectId.value, n.id, at)
}

async function openItem(item: OutlineItem): Promise<void> {
  if (node.value) code.value = await source.chunk(projectId.value, node.value.id, item.start)
}

function pick(n: GraphNode): void {
  query.value = ''
  map?.select(n.id)
  void selectNode(n)
}

const marked = computed(() =>
  code.value && primary.value && primary.value.file === code.value.file ? primary.value.line : null
)

// ---------- 报错 ----------

async function applyFocus(f: WorkbenchFocus): Promise<void> {
  if (f.project && f.project !== projectId.value) projectId.value = f.project
  if (!f.error) return
  errorText.value = f.error
  notice.value = '正在找报错落在哪……'
  const loc = await source.locate(f.error).catch(() => null)
  if (!loc) {
    located.value = null
    notice.value = '这个报错不在登记的项目里（可能是第三方库的代码）。'
    return
  }
  notice.value = ''
  located.value = loc
  if (loc.project !== projectId.value) projectId.value = loc.project // 换项目后 loadGraph 会接着标出来
  else showError(loc)
}

function showError(loc: LocatedError): void {
  if (!map || !graph.value) return
  const h = highlightFrom(loc, graph.value)
  map.setHighlight(h)
  const target = h.primary && graph.value.nodes.find((n) => n.id === h.primary!.file)
  if (target) {
    map.select(target.id)
    void selectNode(target, h.primary!.line)
  }
}

function clearError(): void {
  located.value = null
  errorText.value = ''
  map?.setHighlight(null)
}

// ---------- 问她 ----------

function askFile(): void {
  if (node.value) source.say(`帮我看看 ${node.value.id} 是做什么的，主要有哪些函数。`)
}

function askCode(): void {
  if (code.value) source.say(`帮我讲讲 ${code.value.file} 里的 ${code.value.qname} 是做什么的。`)
}

/** 让她修：交给工作窗口里的编程 Agent（在报错所在的项目里开任务；改文件之前她会在工作窗口里问你） */
function askFix(): void {
  const project = located.value?.project ?? projectId.value
  if (!errorText.value || !project) return
  void source.task(project, lines.fixTask(errorText.value)).then((err) => say(err ? `没开成：${err}` : '交给她了：在任务页里修'))
}

// ---------- 手势 ----------

function say(text: string): void {
  toast.value = text
  clearTimeout(toastTimer)
  toastTimer = window.setTimeout(() => (toast.value = ''), 2800)
}

function closePanel(): void {
  map?.select(null, false)
  node.value = null
}

async function onGesture(ev: GestureEvent): Promise<void> {
  if (!map) return
  switch (ev.type) {
    case 'summon':
      map.frameAll()
      map.summonEffect()
      break
    case 'dismiss':
      // 一层一层收：先收面板，没有面板再关掉星图
      if (node.value) closePanel()
      else emit('close')
      break
    case 'grab': {
      const n = map.pickAt(ev.x, ev.y)
      grabbed.value = n
      if (n) map.select(n.id)
      break
    }
    case 'pull':
      if (grabbed.value) await selectNode(grabbed.value)
      break
    case 'release':
      grabbed.value = null
      break
    case 'rotate':
      map.orbit(ev.dx, ev.dy)
      break
    case 'zoom':
      map.dolly(ev.factor)
      break
    case 'seal':
      map.sealEffect()
      await onSeal()
      break
  }
}

/** 双手合十：有等确认的修改就确认它；没有的话，有报错就让她修 */
async function onSeal(): Promise<void> {
  if (fix.value) {
    const ok = await source.seal(fix.value.id)
    say(ok ? '结印确认：她开始修了（先在新分支上跑测试，全过才改到你的代码里）' : '这份修改已经不在等确认了')
  } else if (located.value) {
    askFix()
    say('结印：让她修这个报错')
  } else {
    say('现在没有等你确认的修改')
  }
}

function gestureFailed(message: string): void {
  say(message)
}

onMounted(async () => {
  map = new StarMap(canvas.value!)
  map.onSelect = (n) => void selectNode(n)
  map.onHover = (n, x, y) => (tip.value = n ? { name: n.id, lines: n.lines, x, y } : null)
  map.onHoverModule = (m, x, y) => (moduleTip.value = m ? { m, x, y } : null)
  if (import.meta.env.DEV) {
    // 开发调试入口：控制台里可以直接定位报错、选星球
    ;(window as unknown as Record<string, unknown>).__wb = {
      map,
      applyFocus,
      selectNode,
      graph,
      gesture: onGesture,
      setFix: (f: PendingFix | null) => (fix.value = f),
      activity: onActivity
    }
  }
  await loadProjects()
  await nextTick()
  const focus = await source.takeFocus()
  if (focus) await applyFocus(focus)
  unsubscribe.push(
    source.onFocus((f) => {
      void source.takeFocus() // 地图开着时直接收到：主进程记着的那份清掉，下次打开地图不再定位旧报错
      void applyFocus(f)
    })
  )
  fix.value = await source.pendingFix().catch(() => null)
  unsubscribe.push(source.onPendingFix((f) => (fix.value = f)))
  unsubscribe.push(source.onActivity(onActivity))
})


// ---------- 回放她的一次任务 ----------

const replayPicking = ref(false)
const replayList = ref<AgentSessionSummary[]>([])
const replay = ref<{ title: string; steps: ReplayStep[] } | null>(null)
const replayIndex = ref(0)
const replayPlaying = ref(false)
const replaySpeed = ref(1)
/** 镜头跟着她走（平移，不缩放） */
const replayFollow = ref(true)
let replayTimer = 0
const REPLAY_STEP_MS = 900
const STEP_ICON: Record<string, string> = { ask: '💬', read: '👁', edit: '✎', run: '▶', search: '⌕', explore: '⇢', other: '·', end: '■' }
const replayStep = computed(() => replay.value?.steps[replayIndex.value] ?? null)

async function openReplayPicker(): Promise<void> {
  const root = projects.value.find((p) => p.id === projectId.value)?.root
  if (!root) return
  replayPicking.value = !replayPicking.value
  if (!replayPicking.value) return
  try {
    replayList.value = sessionsIn(root, await source.sessions())
  } catch (err) {
    say(`读不了任务列表：${(err as Error).message}`)
    replayPicking.value = false
  }
}

async function startReplay(s: AgentSessionSummary): Promise<void> {
  const root = projects.value.find((p) => p.id === projectId.value)?.root
  replayPicking.value = false
  if (!root || !map) return
  try {
    const full = await source.session(s.id)
    const steps = buildReplay(full.events, root, full.cwd)
    if (!steps.length) return say('这个任务里还没有能回放的步骤')
    closePanel()
    replay.value = { title: s.title, steps }
    replayIndex.value = 0
    map.setTouched([])
    applyReplayStep(true)
    playReplay(true)
  } catch (err) {
    say(`读不了这个任务：${(err as Error).message}`)
  }
}

/** 放到第 replayIndex 步：改过的文件标绿（拖动时整体重算），这一步碰的文件闪一下，镜头跟过去 */
function applyReplayStep(seek: boolean): void {
  const r = replay.value
  const step = replayStep.value
  if (!r || !step || !map) return
  if (seek) map.setTouched(editedUpTo(r.steps, replayIndex.value))
  if (step.star) {
    map.touch(step.star, step.kind === 'edit' && step.ok ? 'edit' : 'read')
    if (replayFollow.value) map.follow(step.star)
  }
}

function playReplay(on: boolean): void {
  window.clearInterval(replayTimer)
  replayPlaying.value = on
  if (!on) return
  if (replay.value && replayIndex.value >= replay.value.steps.length - 1) {
    replayIndex.value = 0 // 放完了再点播放：从头来
    applyReplayStep(true)
  }
  replayTimer = window.setInterval(() => {
    const r = replay.value
    if (!r || replayIndex.value >= r.steps.length - 1) return playReplay(false)
    replayIndex.value++
    applyReplayStep(false)
  }, REPLAY_STEP_MS / replaySpeed.value)
}

function seekReplay(i: number): void {
  replayIndex.value = i
  applyReplayStep(true)
}

function setReplaySpeed(speed: number): void {
  replaySpeed.value = speed
  if (replayPlaying.value) playReplay(true)
}

function closeReplay(): void {
  playReplay(false)
  replay.value = null
  map?.clearTouched()
}

onBeforeUnmount(() => {
  for (const off of unsubscribe.splice(0)) off()
  window.clearInterval(replayTimer)
  clearTimeout(toastTimer)
  clearTimeout(activityTimer)
  map?.dispose()
})
</script>

<template>
  <div class="wb">
    <div ref="canvas" class="canvas"></div>

    <header class="top">
      <div class="title">代码星图</div>
      <select v-if="projects.length > 1" v-model="projectId">
        <option v-for="p in projects" :key="p.id" :value="p.id">{{ p.name }}</option>
      </select>
      <span v-else-if="projects.length" class="project">{{ projects[0].name }}</span>
      <div v-if="graph" class="search">
        <input v-model="query" placeholder="找文件……" spellcheck="false" @keydown.enter="results[0] && pick(results[0])" />
        <ul v-if="results.length">
          <li v-for="r in results" :key="r.id" @click="pick(r)">{{ r.id }}</li>
        </ul>
      </div>
      <button v-if="graph" class="ghost" title="拉远看模块，拉近看文件" @click="map?.frameAll()">看全部</button>
      <div v-if="graph" class="replay-pick">
        <button class="ghost" :class="{ on: replayPicking || replay }" title="在星图上回放她做过的一个任务：读了哪些文件、改了哪里" @click="openReplayPicker">回放</button>
        <ul v-if="replayPicking">
          <li v-if="!replayList.length" class="muted">这个项目里还没有做过的任务</li>
          <li v-for="s in replayList.slice(0, 12)" :key="s.id" @click="startReplay(s)">
            {{ s.title }}<span class="muted"> · {{ s.events }} 条记录</span>
          </li>
        </ul>
      </div>
      <button
        v-if="graph && counts?.tests"
        class="ghost"
        :class="{ on: hideTests }"
        :title="hideTests ? '把测试文件显示回来（调暗显示）' : '测试文件现在是调暗的，点一下隐藏'"
        @click="hideTests = !hideTests"
      >
        {{ hideTests ? '显示测试文件' : '隐藏测试文件' }}
      </button>
      <button
        v-if="graph"
        class="ghost"
        :class="{ on: gestures }"
        :title="gestures ? '关掉手势（摄像头随之关闭）' : '用手势操作星图（会打开摄像头，只在本机识别）'"
        @click="gestures = !gestures"
      >
        ✋ 手势{{ gestures ? '开着' : '' }}
      </button>
    </header>

    <div v-if="graph" class="legend">
      <div v-for="[g, c] in legend" :key="g"><i :style="{ background: c }"></i>{{ g }}</div>
      <div class="gap"><b class="line"></b>函数调用</div>
      <div><b class="arc" :style="{ borderColor: HTTP_COLOR }"></b>请求接口（HTTP）</div>
      <div><b class="arc" :style="{ borderColor: IPC_COLOR }"></b>进程间消息（IPC）</div>
      <div class="gap"><i class="bright"></i>亮一点：还没提交的改动</div>
      <div><i :style="{ background: ERROR_COLOR }"></i>报错的文件</div>
      <div><i :style="{ background: CHAIN_COLOR }"></i>调用链上的文件</div>
      <div><i :style="{ background: TOUCH_COLOR }"></i>她这次改过的文件</div>
    </div>

    <div v-if="tip" class="tip" :style="{ left: `${tip.x + 14}px`, top: `${tip.y + 10}px` }">
      {{ tip.name }} · {{ tip.lines }} 行
    </div>
    <div v-else-if="moduleTip" class="tip" :style="{ left: `${moduleTip.x + 14}px`, top: `${moduleTip.y + 10}px` }">
      <b>{{ moduleTip.m.label }}</b> · {{ moduleTip.m.files.length }} 个文件 · {{ moduleTip.m.lines }} 行
      <div class="muted">{{ moduleTip.m.id }} · 点一下飞过去</div>
    </div>

    <div v-if="located || notice" class="error-bar">
      <template v-if="located && primary">
        <b>报错在</b> {{ primary.file }} 的 {{ primary.qname }}（第 {{ primary.line }} 行）
        <span v-if="located.chain.length" class="muted">· 调用链上 {{ located.chain.length }} 个函数所在的文件已标橙</span>
        <button class="fix" @click="askFix">让她修</button>
        <button class="ghost" @click="clearError">收起</button>
      </template>
      <template v-else>{{ notice }}</template>
    </div>

    <div v-if="fix" class="fix-card">
      <div class="fix-title">她准备好了一份修改，等你确认</div>
      <div v-for="(l, i) in fixLines.info" :key="'i' + i" class="fix-info">{{ l.text }}</div>
      <pre v-if="fixLines.diff.length" class="fix-diff"><span
        v-for="(l, i) in fixLines.diff"
        :key="i"
        :class="l.kind"
      >{{ l.text }}
</span></pre>
      <div class="fix-hint">
        {{ gestures ? '双手合十保持一秒确认' : '打开「✋ 手势」后可以双手合十确认' }}，也可以{{
          fix.source === 'agent' ? '在任务页里按「可以」' : '在她的气泡里点「好」或「不要」'
        }}
      </div>
    </div>

    <div v-if="toast" class="toast">{{ toast }}</div>

    <GestureLayer
      v-if="gestures"
      :shifted="!!node"
      @gesture="onGesture"
      @cursor="(p) => map?.hoverAt(p)"
      @failed="gestureFailed"
    />

    <aside v-if="node" class="panel">
      <div class="head">
        <div class="path">{{ node.id }}</div>
        <button class="x" title="关掉" @click="closePanel">×</button>
      </div>
      <div class="meta">
        {{ node.lang }} · {{ node.lines }} 行 · {{ node.functions }} 个函数{{ node.modified ? ' · 有没提交的改动' : '' }}
      </div>
      <div class="actions">
        <button @click="askFile">问她这个文件</button>
        <button v-if="code" @click="askCode">问她这段代码</button>
      </div>
      <div v-if="cross && (cross.out.length || cross.in.length)" class="cross">
        <div v-for="l in cross.out" :key="'o' + l.target + l.kind" class="cross-row" @click="jump(l.target)">
          <span class="dir" :style="{ color: l.kind === 'ipc' ? IPC_COLOR : HTTP_COLOR }">{{ l.kind === 'ipc' ? '发消息给' : '请求' }} →</span>
          <span class="who">{{ l.target }}</span>
          <span class="via">{{ (l.via ?? []).join('、') }}</span>
        </div>
        <div v-for="l in cross.in" :key="'i' + l.source + l.kind" class="cross-row" @click="jump(l.source)">
          <span class="dir" :style="{ color: l.kind === 'ipc' ? IPC_COLOR : HTTP_COLOR }">← {{ l.kind === 'ipc' ? '收到消息' : '被请求' }}</span>
          <span class="who">{{ l.source }}</span>
          <span class="via">{{ (l.via ?? []).join('、') }}</span>
        </div>
      </div>
      <ul class="outline">
        <li
          v-for="item in outline"
          :key="item.qname + item.start"
          :class="{ on: code?.qname === item.qname }"
          :title="item.doc || item.signature"
          @click="openItem(item)"
        >
          <span class="kind">{{ kinds[item.kind] ?? item.kind }}</span>{{ item.qname }}
        </li>
      </ul>
      <div v-if="code" class="code">
        <div class="code-title">{{ code.qname }} · 第 {{ code.start }}–{{ code.end }} 行</div>
        <pre><code><span
          v-for="(line, i) in code.lines"
          :key="i"
          :class="{ hit: marked === code.start + i }"
        ><em>{{ code.start + i }}</em>{{ line }}
</span></code></pre>
        <div v-if="code.truncated" class="muted">……（太长，只显示前 200 行）</div>
      </div>
    </aside>

    <div v-if="!projects.length && !status" class="empty">
      <p>还没有登记项目。登记以后，项目里的每个文件都会变成一颗星球。</p>
      <button @click="source.openSettings()">去「设置 → 代码库」添加项目</button>
    </div>

    <div v-if="replay" class="replay-bar">
      <div class="replay-title">回放：{{ replay.title }}</div>
      <div class="replay-controls">
        <button :title="replayPlaying ? '暂停' : '播放'" @click="playReplay(!replayPlaying)">{{ replayPlaying ? '❚❚' : '▶' }}</button>
        <button v-for="sp in [1, 2, 4]" :key="sp" class="ghost speed" :class="{ on: replaySpeed === sp }" @click="setReplaySpeed(sp)">{{ sp }}×</button>
        <input
          type="range"
          min="0"
          :max="replay.steps.length - 1"
          :value="replayIndex"
          aria-label="回放进度"
          @input="seekReplay(Number(($event.target as HTMLInputElement).value))"
        />
        <label class="follow"><input v-model="replayFollow" type="checkbox" />镜头跟着走</label>
        <button class="ghost" title="关掉回放" @click="closeReplay">×</button>
      </div>
      <div v-if="replayStep" class="replay-step" :class="[replayStep.kind, { bad: !replayStep.ok }]">
        第 {{ replayIndex + 1 }}/{{ replay.steps.length }} 步 · {{ STEP_ICON[replayStep.kind] }} {{ replayStep.label }}
        <span v-if="!replayStep.ok && replayStep.kind !== 'end'" class="muted">（没成）</span>
      </div>
    </div>

    <footer class="status">
      <template v-if="status">{{ status }}</template>
      <template v-else-if="counts">
        {{ counts.modules }} 个模块 · {{ counts.files }} 个文件 · {{ counts.calls }} 条调用光线 ·
        {{ counts.cross }} 条跨进程弧线 ·
        {{ gestures ? '张开手掌旋转、捏住星球往面前拉看代码' : '拉远看模块、拉近看文件，点模块飞过去，点星球看代码' }}
        <span v-if="activity" class="activity"> · {{ activity }}</span>
      </template>
    </footer>
  </div>
</template>

<style>
/* 星球名字（StarMap 里生成的 HTML 元素） */
.star-label.error {
  color: #ffc2c7 !important;
  border: 1px solid rgba(255, 59, 74, 0.7);
}
</style>

<style scoped>
.wb {
  position: relative;
  width: 100%;
  height: 100%;
  overflow: hidden;
  background: #04060d;
  color: #d8e6ff;
  font-size: 13px;
}

/* z-index 让星球名字（各自带 z-index）关在这一层里，不会盖住面板 */
.canvas {
  position: absolute;
  inset: 0;
  z-index: 0;
}

.top {
  position: absolute;
  top: 14px;
  left: 16px;
  display: flex;
  gap: 10px;
  align-items: center;
}

.title {
  font-size: 17px;
  font-weight: 600;
  letter-spacing: 2px;
  color: #9fe6ff;
  text-shadow: 0 0 12px rgba(90, 209, 255, 0.6);
}

.project {
  color: #8fa4c8;
}

select,
input,
button {
  font: inherit;
  color: #d8e6ff;
  background: rgba(10, 20, 40, 0.75);
  border: 1px solid rgba(90, 209, 255, 0.35);
  border-radius: 6px;
  padding: 4px 10px;
  outline: none;
}

button {
  cursor: pointer;
}

button:hover {
  border-color: rgba(90, 209, 255, 0.8);
  box-shadow: 0 0 10px rgba(90, 209, 255, 0.35);
}

button.ghost {
  background: transparent;
}

button.on {
  border-color: rgba(125, 255, 239, 0.9);
  color: #7dffef;
  box-shadow: 0 0 10px rgba(125, 255, 239, 0.4);
}

.search {
  position: relative;
}

.search input {
  width: 220px;
}

.search ul {
  position: absolute;
  top: 100%;
  left: 0;
  margin: 4px 0 0;
  padding: 4px 0;
  min-width: 320px;
  list-style: none;
  background: rgba(8, 16, 32, 0.95);
  border: 1px solid rgba(90, 209, 255, 0.35);
  border-radius: 6px;
}

.search li {
  padding: 4px 10px;
  cursor: pointer;
  white-space: nowrap;
}

.search li:hover {
  background: rgba(90, 209, 255, 0.15);
}

.legend {
  position: absolute;
  top: 56px;
  left: 16px;
  display: flex;
  flex-direction: column;
  gap: 3px;
  color: #8fa4c8;
  font-size: 12px;
  pointer-events: none;
}

.legend i {
  display: inline-block;
  width: 9px;
  height: 9px;
  margin-right: 6px;
  border-radius: 50%;
  box-shadow: 0 0 6px currentColor;
}

.legend i.bright {
  background: #fff;
}

.legend .gap {
  margin-top: 5px;
}

/* 光线的图例：直线是调用，弧线是跨进程 */
.legend b {
  display: inline-block;
  width: 16px;
  margin-right: 5px;
  vertical-align: middle;
}

.legend b.line {
  height: 0;
  border-top: 1px solid #8fa4c8;
}

.legend b.arc {
  height: 7px;
  border: 1.5px solid;
  border-bottom: none;
  border-radius: 9px 9px 0 0;
}

/* 面板里的跨进程光线 */
.cross {
  margin-bottom: 8px;
  padding: 6px 0;
  border-top: 1px solid rgba(90, 209, 255, 0.15);
  max-height: 22%;
  overflow-y: auto;
}

.cross-row {
  display: flex;
  gap: 6px;
  align-items: baseline;
  padding: 2px 4px;
  cursor: pointer;
  white-space: nowrap;
}

.cross-row:hover {
  background: rgba(90, 209, 255, 0.12);
}

.cross-row .dir {
  flex: none;
  font-size: 12px;
}

.cross-row .who {
  overflow: hidden;
  text-overflow: ellipsis;
}

.cross-row .via {
  flex: none;
  max-width: 40%;
  overflow: hidden;
  text-overflow: ellipsis;
  color: #8fa4c8;
  font-size: 12px;
}

.tip {
  position: absolute;
  padding: 3px 8px;
  border-radius: 4px;
  background: rgba(8, 16, 32, 0.9);
  border: 1px solid rgba(90, 209, 255, 0.3);
  pointer-events: none;
  white-space: nowrap;
}

.error-bar {
  position: absolute;
  bottom: 38px;
  left: 16px;
  display: flex;
  gap: 8px;
  align-items: center;
  max-width: calc(100% - 500px);
  min-width: 300px;
  padding: 7px 14px;
  border-radius: 8px;
  background: rgba(60, 8, 16, 0.82);
  border: 1px solid rgba(255, 59, 74, 0.6);
  box-shadow: 0 0 18px rgba(255, 59, 74, 0.35);
}

.error-bar button.fix {
  border-color: rgba(255, 120, 120, 0.8);
  background: rgba(255, 59, 74, 0.25);
}

.muted {
  color: #8fa4c8;
}

/* 等确认的修改 */
.fix-card {
  position: absolute;
  left: 16px;
  bottom: 84px;
  width: 520px;
  max-width: calc(100% - 520px);
  max-height: 48%;
  display: flex;
  flex-direction: column;
  padding: 10px 14px;
  border-radius: 10px;
  background: rgba(30, 22, 6, 0.82);
  border: 1px solid rgba(255, 210, 122, 0.55);
  box-shadow: 0 0 22px rgba(255, 210, 122, 0.22);
}

.fix-title {
  margin-bottom: 4px;
  font-weight: 600;
  color: #ffe2a8;
}

.fix-info {
  color: #e9dcc0;
}

.fix-diff {
  flex: 1 1 auto;
  min-height: 0;
  margin: 6px 0;
  padding: 6px 8px;
}

.fix-diff span {
  padding: 0;
}

.fix-diff .add {
  color: #8dffb0;
  background: rgba(60, 200, 110, 0.12);
}

.fix-diff .del {
  color: #ff9aa2;
  background: rgba(255, 59, 74, 0.12);
}

.fix-diff .hunk {
  color: #7dc8ff;
}

.fix-diff .file {
  color: #c9b98f;
}

.fix-hint {
  color: #c9b98f;
  font-size: 12px;
}

.toast {
  position: absolute;
  top: 56px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 4;
  padding: 6px 16px;
  border-radius: 8px;
  background: rgba(8, 16, 32, 0.9);
  border: 1px solid rgba(125, 255, 239, 0.5);
  color: #dffcff;
  box-shadow: 0 0 16px rgba(125, 255, 239, 0.25);
  pointer-events: none;
}

/* 全息面板 */
.panel {
  position: absolute;
  top: 14px;
  right: 14px;
  bottom: 40px;
  width: 440px;
  display: flex;
  flex-direction: column;
  padding: 12px 14px;
  border-radius: 10px;
  background: rgba(8, 16, 32, 0.72);
  border: 1px solid rgba(90, 209, 255, 0.4);
  box-shadow:
    0 0 24px rgba(90, 209, 255, 0.18),
    inset 0 0 30px rgba(90, 209, 255, 0.06);
  backdrop-filter: blur(6px);
}

.head {
  display: flex;
  justify-content: space-between;
  gap: 8px;
}

.path {
  font-weight: 600;
  color: #e8f3ff;
  word-break: break-all;
}

.x {
  padding: 0 8px;
  background: transparent;
  border: none;
  font-size: 18px;
  line-height: 1;
}

.meta {
  margin: 4px 0 8px;
  color: #8fa4c8;
}

.actions {
  display: flex;
  gap: 8px;
  margin-bottom: 8px;
}

.outline {
  flex: 0 1 auto;
  max-height: 32%;
  overflow-y: auto;
  margin: 0 0 8px;
  padding: 0;
  list-style: none;
  border-top: 1px solid rgba(90, 209, 255, 0.15);
}

.outline li {
  padding: 3px 4px;
  cursor: pointer;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.outline li:hover,
.outline li.on {
  background: rgba(90, 209, 255, 0.14);
}

.kind {
  display: inline-block;
  min-width: 34px;
  margin-right: 6px;
  color: #7dffef;
  font-size: 11px;
}

.code {
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.code-title {
  margin-bottom: 4px;
  color: #9fe6ff;
}

pre {
  flex: 1;
  margin: 0;
  overflow: auto;
  padding: 6px 0;
  border-radius: 6px;
  background: rgba(2, 6, 14, 0.7);
  font-family: Consolas, 'Microsoft YaHei', monospace;
  font-size: 12px;
  line-height: 1.5;
}

pre span {
  display: block;
  padding-right: 8px;
  white-space: pre;
}

pre em {
  display: inline-block;
  width: 42px;
  margin-right: 10px;
  padding-right: 6px;
  text-align: right;
  font-style: normal;
  color: #4d5d80;
  border-right: 1px solid rgba(90, 209, 255, 0.12);
}

pre span.hit {
  background: rgba(255, 59, 74, 0.28);
}

pre span.hit em {
  color: #ff8a92;
}

.empty {
  position: absolute;
  top: 45%;
  left: 50%;
  transform: translate(-50%, -50%);
  text-align: center;
  color: #8fa4c8;
}

/* 回放：项目里的任务列表、底部的时间轴 */
.replay-pick {
  position: relative;
}

.replay-pick ul {
  position: absolute;
  top: 100%;
  left: 0;
  z-index: 3;
  min-width: 320px;
  max-height: 360px;
  margin: 4px 0 0;
  padding: 4px 0;
  overflow-y: auto;
  list-style: none;
  background: rgba(8, 16, 32, 0.95);
  border: 1px solid rgba(90, 209, 255, 0.35);
  border-radius: 6px;
}

.replay-pick li {
  padding: 5px 10px;
  cursor: pointer;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.replay-pick li:hover {
  background: rgba(90, 209, 255, 0.15);
}

.replay-bar {
  position: absolute;
  left: 16px;
  right: 470px;
  bottom: 36px;
  z-index: 2;
  padding: 8px 12px;
  border-radius: 10px;
  background: rgba(8, 16, 32, 0.85);
  border: 1px solid rgba(90, 209, 255, 0.4);
  box-shadow: 0 0 18px rgba(90, 209, 255, 0.18);
}

.replay-title {
  margin-bottom: 4px;
  color: #9fe6ff;
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.replay-controls {
  display: flex;
  align-items: center;
  gap: 8px;
}

.replay-controls input[type='range'] {
  flex: 1;
  min-width: 120px;
  accent-color: #5ad1ff;
}

.replay-controls .speed {
  padding: 2px 7px;
  font-size: 12px;
}

.replay-controls .follow {
  display: flex;
  align-items: center;
  gap: 4px;
  color: #8fa4c8;
  font-size: 12px;
  white-space: nowrap;
}

.replay-step {
  margin-top: 6px;
  font-family: Consolas, 'Microsoft YaHei', monospace;
  font-size: 12.5px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.replay-step.read {
  color: #67e8f9;
}

.replay-step.edit {
  color: #4ade80;
}

.replay-step.ask {
  color: #ffe9a8;
}

.replay-step.bad {
  color: #ff8a92;
}

.status .activity {
  color: #86efac;
}

.status {
  position: absolute;
  left: 16px;
  bottom: 12px;
  color: #6f7da8;
  font-size: 12px;
  pointer-events: none;
}
</style>
