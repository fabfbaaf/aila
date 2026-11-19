<script setup lang="ts">
/**
 * 设置 → 扩展：导入扩展包（zip 或文件夹）、看清单再装、开关、填设置、开停工具服务、卸载。
 * 导入时文件由主进程弹窗选、AI 服务检查；这里显示的清单（会加什么、会运行什么程序）是代码从说明书算出来的。
 * 密钥类设置只显示填没填，不回显。
 */
import { onActivated, onMounted, ref } from 'vue'
import { summarize, type CatalogListing, type ExtensionInfo, type ExtensionPlan, type ExtensionUpdate } from '../../../../shared/extensions'
import { MCP_STATUS_LABELS } from '../../../../shared/mcp'
import { bridge } from '../../platform/bridge'
import Toggle from '../Toggle.vue'

const items = ref<ExtensionInfo[]>([])
const folder = ref('')
const loading = ref(false)
const busy = ref('')
const error = ref('')
const notice = ref('')
const open = ref<Record<string, boolean>>({})
const confirmRemove = ref('')
/** 选好、检查过、还没装的包 */
const staged = ref<{ token: string; plan: ExtensionPlan } | null>(null)
/** 设置表单里正在改的值（扩展 id → 键 → 值） */
const drafts = ref<Record<string, Record<string, string>>>({})

type Result<T> = { ok: boolean; data?: T; error?: string }

async function run<T>(label: string, action: () => Promise<Result<T>>, done = ''): Promise<T | undefined> {
  busy.value = label
  error.value = ''
  notice.value = ''
  try {
    const res = await action()
    if (!res.ok) {
      error.value = res.error ?? '没成功，重试一下。'
      return undefined
    }
    notice.value = done
    return res.data
  } finally {
    busy.value = ''
  }
}

function apply(data: { extensions: ExtensionInfo[]; folder: string } | undefined): void {
  if (!data) return
  items.value = data.extensions
  folder.value = data.folder
  for (const e of data.extensions) {
    drafts.value[e.id] = Object.fromEntries(e.settings.map((s) => [s.key, s.secret ? '' : s.value]))
  }
}

async function load(): Promise<void> {
  if (!bridge) return
  loading.value = true
  apply(await run('', () => bridge!.extList()))
  loading.value = false
}

async function pick(kind: 'zip' | 'folder'): Promise<void> {
  if (staged.value) await discard()
  const data = await run(`pick:${kind}`, () => bridge!.extPick(kind))
  if (data) staged.value = data
}

async function install(): Promise<void> {
  const s = staged.value
  if (!s) return
  const servers = s.plan.servers.length ? '它的工具服务先停着，在下面打开。' : ''
  const data = await run('install', () => bridge!.extInstall(s.token), `装好了「${s.plan.name}」。${servers}新开的会话里就能用。`)
  if (data) {
    staged.value = null
    apply(data)
    open.value[data.installed] = true
  }
}

async function discard(): Promise<void> {
  const s = staged.value
  staged.value = null
  if (s) await bridge!.extDiscard(s.token)
}

async function update(e: ExtensionInfo, patch: ExtensionUpdate, done: string): Promise<void> {
  apply(await run(`update:${e.id}`, () => bridge!.extUpdate(e.id, patch), done))
}

async function saveSettings(e: ExtensionInfo): Promise<void> {
  const draft = drafts.value[e.id] ?? {}
  // 密钥类没填新值的不发（发空的会清掉）
  const settings = Object.fromEntries(e.settings.filter((s) => !s.secret || draft[s.key]).map((s) => [s.key, draft[s.key] ?? '']))
  await update(e, { settings }, '设置保存了。开着的工具服务会用新设置重启。')
}

async function clearSetting(e: ExtensionInfo, key: string): Promise<void> {
  await update(e, { settings: { [key]: '' } }, '清掉了。')
}

async function remove(e: ExtensionInfo): Promise<void> {
  if (confirmRemove.value !== e.id) {
    confirmRemove.value = e.id
    return
  }
  confirmRemove.value = ''
  apply(await run(`remove:${e.id}`, () => bridge!.extRemove(e.id), `卸载了「${e.name}」，它的工具服务也停了。`))
}

// ----- 第二、三期：台词、形象、页面 -----

const LINE_NAMES: Record<string, string> = {
  chatOpen: '打开对话', pokeHead: '被戳头', pokeHeadMany: '一直被戳', pokeBody: '被戳身上', dragStart: '被拖起来',
  dragEnd: '被放下', wake: '睡醒', sleepy: '犯困', wakeHere: '被叫醒', forgetOne: '忘掉一件事', agentStart: '开始做任务',
  agentTaskStarted: '任务开始了', testsRecovered: '测试修好了', testsStruggling: '测试一直挂', workMaking: '在写作品', lateNight: '深夜'
}

function linesText(lines: Record<string, number>, replace: boolean): string {
  const parts = Object.entries(lines).map(([k, n]) => `${LINE_NAMES[k] ?? k} ${n} 句`)
  return (replace ? '整组换掉：' : '加进去：') + parts.join('、')
}

function mb(bytes: number): string {
  return bytes >= 1e6 ? `${(bytes / 1e6).toFixed(1)}MB` : `${Math.max(1, Math.round(bytes / 1e3))}KB`
}

function isEmpty(p: ExtensionPlan): boolean {
  return !p.commands.length && !p.skills.length && !p.servers.length && !p.motions.length && !Object.keys(p.lines).length
    && !p.avatars.length && !p.pages.length
}

async function useAvatar(e: ExtensionInfo, name: string): Promise<void> {
  await run(`avatar:${e.id}`, () => bridge!.extUseAvatar(e.id, name), `换上了「${name}」。想换回来就去「形象」点「换回艾拉自己的形象」。`)
}

async function openPage(e: ExtensionInfo, name: string, title: string): Promise<void> {
  await run('', () => bridge!.extOpenPage(e.id, name, title))
}

// ----- 第三期：目录 -----

const catalogs = ref<CatalogListing['catalogs']>([])
const sourcesText = ref('')
const catalogLoading = ref(false)

async function loadCatalog(): Promise<void> {
  if (!bridge) return
  catalogLoading.value = true
  const data = await run('', () => bridge!.extCatalog())
  catalogLoading.value = false
  if (data) {
    catalogs.value = data.catalogs
    sourcesText.value = data.sources.join('\n')
  }
}

async function saveSources(): Promise<void> {
  const urls = sourcesText.value.split(/\r?\n/).map((s) => s.trim()).filter(Boolean)
  catalogLoading.value = true
  const data = await run('sources', () => bridge!.extCatalogSources(urls), urls.length ? '目录地址保存了。' : '目录地址都清掉了。')
  catalogLoading.value = false
  if (data) catalogs.value = data.catalogs
}

async function fromCatalog(source: string, id: string): Promise<void> {
  if (staged.value) await discard()
  const data = await run(`catalog:${id}`, () => bridge!.extCatalogInspect(source, id))
  if (data) {
    staged.value = data
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }
}

onMounted(load)
onActivated(load)
</script>

<template>
  <h2>扩展</h2>
  <p class="lead">
    扩展包能一次给她加上新的命令、做事的本事和外部工具。只装你信得过的：装之前会列出它会加什么、会在电脑上运行什么程序。
  </p>

  <div v-if="!bridge" class="offline">扩展要在桌面版里管理。</div>

  <template v-else>
    <div class="card">
      <div class="heading">
        <h3>导入扩展包</h3>
        <div class="actions-inline">
          <button class="btn primary" :disabled="Boolean(busy)" @click="pick('zip')">
            {{ busy === 'pick:zip' ? '正在检查…' : '选一个 .zip' }}
          </button>
          <button class="btn" :disabled="Boolean(busy)" @click="pick('folder')">
            {{ busy === 'pick:folder' ? '正在检查…' : '选一个文件夹' }}
          </button>
        </div>
      </div>
      <p class="hint">
        扩展包是一个带 <code>aila-extension.json</code> 的文件夹（或者把它压成 zip）。仓库里的
        <code>examples/extensions</code> 有三个例子；怎么写见 <code>docs/extensions.md</code>。
      </p>

      <div v-if="staged" class="plan" role="region" aria-label="装之前的清单">
        <div class="plan-head">
          <b>{{ staged.plan.name }}</b>
          <span class="ver">{{ staged.plan.id }} · {{ staged.plan.version }}</span>
          <span v-if="staged.plan.upgrade" class="tag warn">升级</span>
        </div>
        <p v-if="staged.plan.description" class="desc">{{ staged.plan.description }}</p>
        <ul class="what">
          <li v-if="staged.plan.commands.length">
            <span class="k">命令</span>
            <span><code v-for="c in staged.plan.commands" :key="c" class="chip">/{{ c }}</code></span>
          </li>
          <li v-for="s in staged.plan.skills" :key="s.name">
            <span class="k">本事</span><span><b>{{ s.name }}</b>：{{ s.description }}</span>
          </li>
          <li v-for="s in staged.plan.servers" :key="s.name">
            <span class="k">会运行</span>
            <span>
              <code class="cmd">{{ s.command }}</code>
              <span v-if="s.readOnly" class="tag good">只读工具</span>
              <span v-if="s.env.length" class="env">环境变量：{{ s.env.join('、') }}</span>
            </span>
          </li>
          <li v-for="s in staged.plan.settings" :key="s.key">
            <span class="k">要填</span>
            <span>{{ s.label }}<template v-if="s.secret">（密钥）</template><template v-if="s.required">，必填</template></span>
          </li>
          <li v-if="staged.plan.motions.length">
            <span class="k">动作</span>
            <span><span v-for="m in staged.plan.motions" :key="m.name" class="chip">[{{ m.name }}] {{ m.label }}</span></span>
          </li>
          <li v-if="Object.keys(staged.plan.lines).length">
            <span class="k">台词</span>
            <span>{{ linesText(staged.plan.lines, staged.plan.linesReplace) }}</span>
          </li>
          <li v-if="staged.plan.avatars.length">
            <span class="k">形象</span>
            <span>{{ staged.plan.avatars.map((a) => `${a.name}（${mb(a.size)}）`).join('、') }}</span>
          </li>
          <li v-if="staged.plan.pages.length">
            <span class="k">页面</span>
            <span>{{ staged.plan.pages.map((p) => p.title).join('、') }}</span>
          </li>
          <li v-if="isEmpty(staged.plan)">
            <span class="k"></span><span class="muted">里面什么都没有</span>
          </li>
        </ul>
        <p v-for="w in staged.plan.warnings" :key="w" class="msg warn">{{ w }}</p>
        <div class="actions">
          <button class="btn primary" :disabled="Boolean(busy)" @click="install">
            {{ busy === 'install' ? '正在装…' : staged.plan.upgrade ? '升级' : '装' }}
          </button>
          <button class="btn" :disabled="busy === 'install'" @click="discard">不装了</button>
        </div>
      </div>

      <p v-if="error" class="msg error" role="alert">{{ error }}</p>
      <p v-if="notice" class="msg ok" role="status">{{ notice }}</p>
    </div>

    <div class="card">
      <div class="heading">
        <h3>装好的扩展</h3>
        <button class="btn" :disabled="loading || Boolean(busy)" @click="load">{{ loading ? '正在读取…' : '刷新' }}</button>
      </div>
      <p v-if="loading && !items.length" class="empty" role="status">正在读取…</p>
      <p v-else-if="!items.length" class="empty">还没装扩展。</p>

      <ul class="exts">
        <li v-for="e in items" :key="e.id" class="ext" :class="{ off: !e.enabled }">
          <div class="ext-head">
            <Toggle :model-value="e.enabled" @update:model-value="(v) => update(e, { enabled: v }, v ? `启用了「${e.name}」，新开的会话里能用。` : `停用了「${e.name}」：命令、本事、工具都不给她了。`)" />
            <b>{{ e.name }}</b>
            <span class="ver">{{ e.id }} · {{ e.version }}</span>
            <span class="sum">{{ summarize(e) }}</span>
            <span class="spacer"></span>
            <button class="link" @click="open[e.id] = !open[e.id]">{{ open[e.id] ? '收起' : '详细' }}</button>
            <button class="btn danger" :disabled="Boolean(busy)" @click="remove(e)">
              {{ confirmRemove === e.id ? '再点一次卸载' : '卸载' }}
            </button>
          </div>
          <p v-if="e.description" class="desc">{{ e.description }}</p>
          <p v-if="e.missing.length" class="msg warn">还没填：{{ e.missing.join('、') }}（填了才能打开它的工具服务）</p>

          <div v-if="open[e.id]" class="detail">
            <div v-if="e.commands.length" class="block">
              <span class="k">命令</span>
              <span><code v-for="c in e.commands" :key="c" class="chip">/{{ c }}</code></span>
            </div>
            <div v-for="s in e.skills" :key="s.name" class="block">
              <span class="k">本事</span><span><b>{{ s.name }}</b>：{{ s.description }}</span>
            </div>
            <div v-if="e.motions.length" class="block">
              <span class="k">动作</span>
              <span>
                <span v-for="m in e.motions" :key="m.name" class="chip">
                  [{{ m.name }}] {{ m.label }}
                  <button class="link" :disabled="!e.enabled" title="让桌面上的她做一遍" @click="bridge!.extPlay(m.name)">试一下</button>
                </span>
                <span class="hint">对话里她也会用（写成 [{{ e.motions[0].name }}] 这样的标签）</span>
              </span>
            </div>
            <div v-if="Object.keys(e.lines).length" class="block">
              <span class="k">台词</span><span>{{ linesText(e.lines, e.linesReplace) }}</span>
            </div>
            <div v-for="a in e.avatars" :key="a.name" class="block">
              <span class="k">形象</span>
              <span>
                {{ a.name }}（{{ mb(a.size) }}）
                <button class="link" :disabled="!e.enabled || Boolean(busy)" @click="useAvatar(e, a.name)">换上</button>
              </span>
            </div>
            <div v-for="p in e.pages" :key="p.name" class="block">
              <span class="k">页面</span>
              <span>
                {{ p.title }}
                <button class="link" :disabled="!e.enabled" @click="openPage(e, p.name, p.title)">打开</button>
              </span>
            </div>

            <div v-for="s in e.servers" :key="s.name" class="server">
              <div class="server-head">
                <i class="dot" :class="s.status" aria-hidden="true"></i>
                <b>{{ s.name }}</b>
                <span class="state" :class="s.status">{{ MCP_STATUS_LABELS[s.status] ?? s.status }}</span>
                <span v-if="s.readOnly" class="tag good">只读工具</span>
                <span class="spacer"></span>
                <button class="btn" :disabled="Boolean(busy) || !e.enabled || (s.disabled && e.missing.length > 0)"
                  @click="update(e, { servers: { [s.name]: !s.disabled } }, s.disabled ? `打开了 ${s.name}。` : `停了 ${s.name}，她不会再看到它的工具。`)">
                  {{ busy === `update:${e.id}` ? '…' : s.disabled ? '打开' : '停用' }}
                </button>
              </div>
              <code v-if="s.command" class="cmd">{{ s.command }}</code>
              <p v-if="s.error" class="msg error">{{ s.error }}</p>
              <ul v-if="s.tools.length" class="tools">
                <li v-for="t in s.tools" :key="t.name">
                  <code>{{ t.name }}</code>
                  <span v-if="t.readOnly" class="tag good">只读，不问</span>
                  <span v-else-if="t.alwaysAllow" class="tag">以后都不问</span>
                  <span v-else class="tag">每次问</span>
                  <span class="desc">{{ t.description }}</span>
                </li>
              </ul>
              <details v-if="s.log.length" class="log">
                <summary>最近的输出</summary>
                <pre>{{ s.log.join('\n') }}</pre>
              </details>
            </div>

            <div v-if="e.settings.length" class="settings">
              <div v-for="s in e.settings" :key="s.key" class="row">
                <span class="label">{{ s.label }}<template v-if="s.required"> *</template></span>
                <div>
                  <input v-model="drafts[e.id][s.key]" :type="s.secret ? 'password' : 'text'" autocomplete="off"
                    :placeholder="s.secret && s.set ? '已经填了（不显示），要换就填新的' : ''" />
                  <button v-if="s.secret && s.set" class="link" @click="clearSetting(e, s.key)">清掉</button>
                  <span v-if="s.description" class="hint">{{ s.description }}</span>
                </div>
              </div>
              <div class="actions">
                <button class="btn" :disabled="Boolean(busy)" @click="saveSettings(e)">保存设置</button>
              </div>
            </div>
          </div>
        </li>
      </ul>
      <p v-if="folder" class="hint where">装在 <code>{{ folder }}</code></p>
    </div>

    <div class="card">
      <div class="heading">
        <h3>目录</h3>
        <button class="btn" :disabled="catalogLoading || Boolean(busy)" @click="loadCatalog">
          {{ catalogLoading ? '正在读取…' : catalogs.length ? '刷新' : '看看目录' }}
        </button>
      </div>
      <p class="hint">
        目录是一份 <code>catalog.json</code>，列着能装的扩展。地址可以是 https 网址，也可以是这台电脑上的完整路径，一行一个。
        从目录装和导入一样：下载、核对 sha256、先给你看清单。现在还没有官方目录；仓库里的
        <code>examples/extensions/make_catalog.py</code> 能生成一份本机目录试试。
      </p>
      <textarea v-model="sourcesText" rows="3" class="sources" placeholder="https://example.com/aila/catalog.json&#10;D:\aila-extensions\catalog.json"></textarea>
      <div class="actions">
        <button class="btn" :disabled="Boolean(busy)" @click="saveSources">{{ busy === 'sources' ? '正在保存…' : '保存目录地址' }}</button>
      </div>
      <div v-for="c in catalogs" :key="c.source" class="catalog">
        <div class="catalog-head"><b>{{ c.name }}</b><span v-if="c.name !== c.source" class="ver">{{ c.source }}</span></div>
        <p v-if="c.error" class="msg error">{{ c.error }}</p>
        <p v-else-if="!c.extensions.length" class="empty">这个目录里没有扩展。</p>
        <ul class="entries">
          <li v-for="x in c.extensions" :key="x.id">
            <div class="entry-head">
              <b>{{ x.name }}</b>
              <span class="ver">{{ x.id }} · {{ x.version }}<template v-if="x.size"> · {{ mb(x.size) }}</template></span>
              <span v-if="x.installed && !x.upgrade" class="tag good">装了</span>
              <span v-else-if="x.upgrade" class="tag warn">有新版（装的是 {{ x.installed }}）</span>
              <span class="spacer"></span>
              <button class="btn" :disabled="Boolean(busy) || Boolean(x.installed && !x.upgrade)" @click="fromCatalog(c.source, x.id)">
                {{ busy === `catalog:${x.id}` ? '正在下载…' : x.upgrade ? '看看新版' : '看看' }}
              </button>
            </div>
            <p v-if="x.description" class="desc">{{ x.description }}</p>
          </li>
        </ul>
      </div>
    </div>
  </template>
</template>

<style scoped>
.heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.heading h3 { margin-bottom: 0; }
.actions-inline { display: flex; gap: 8px; }
.hint code, .where code { font: 12px var(--ui-mono, Consolas, monospace); }
.plan { margin-top: 14px; padding: 14px; border: 1px dashed var(--field-line); border-radius: var(--ui-r, 9px); }
.plan-head, .ext-head, .server-head { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.ver, .sum, .env, .muted { font-size: 12px; color: var(--muted); }
.desc { margin: 6px 0; color: var(--label); }
.what, .exts, .tools { margin: 0; padding: 0; list-style: none; }
.what li, .block { display: flex; gap: 10px; padding: 5px 0; }
.k { flex: 0 0 48px; color: var(--muted); font-size: 12px; padding-top: 2px; }
.chip { display: inline-block; margin: 0 6px 4px 0; padding: 1px 7px; border-radius: 4px; background: var(--tag-bg); font: 12px var(--ui-mono, Consolas, monospace); }
.cmd { display: block; margin-top: 4px; font: 12px/1.5 var(--ui-mono, Consolas, monospace); color: var(--ui-cyan, #67e8f9); overflow-wrap: anywhere; }
.msg.warn { color: var(--warn); }
.ext { padding: 12px 0; border-top: 1px solid var(--line); }
.ext:first-child { border-top: 0; }
.ext.off b { color: var(--muted); }
.ext-head .btn, .server-head .btn { padding: 3px 10px; font-size: 12px; }
.spacer { flex: 1; }
.detail { margin-top: 8px; padding: 10px 12px; border-radius: var(--ui-r-s, 6px); background: var(--bg); }
.server { padding: 8px 0; border-top: 1px solid var(--line); }
.dot { width: 8px; height: 8px; border-radius: 50%; background: var(--x); }
.dot.ready { background: var(--ok); box-shadow: 0 0 0 3px var(--good-bg); }
.dot.starting { background: var(--accent); animation: pulse 1s infinite; }
.dot.failed, .dot.unsupported { background: var(--danger); }
@keyframes pulse { 50% { opacity: 0.35; } }
.state { font-size: 12px; color: var(--muted); }
.state.ready { color: var(--ok); }
.state.failed { color: var(--danger); }
.tools { margin-top: 6px; }
.tools li { display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px 8px; padding: 3px 0; }
.tools code { font: 12px var(--ui-mono, Consolas, monospace); }
.tools .desc { flex-basis: 100%; margin: 0; font-size: 12px; color: var(--muted); }
.log summary { margin-top: 6px; color: var(--muted); font-size: 12px; cursor: pointer; }
.log pre { max-height: 160px; overflow: auto; margin: 6px 0 0; padding: 8px 10px; border-radius: var(--ui-r-s, 6px); background: var(--surface); font: 11.5px/1.5 var(--ui-mono, Consolas, monospace); white-space: pre-wrap; }
.settings { margin-top: 10px; padding-top: 6px; border-top: 1px solid var(--line); }
.settings input { width: 280px; }
.where { margin-top: 10px; }
.chip .link { margin-left: 4px; font-size: 12px; }
.sources {
  box-sizing: border-box;
  width: 100%;
  margin-top: 8px;
  padding: 8px 10px;
  border: 1px solid var(--field-line);
  border-radius: var(--ui-r-s, 6px);
  background: var(--bg);
  color: var(--text);
  font: 12.5px/1.55 var(--ui-mono, Consolas, monospace);
  resize: vertical;
}
.catalog { margin-top: 14px; padding-top: 10px; border-top: 1px solid var(--line); }
.catalog-head, .entry-head { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.entries { margin: 6px 0 0; padding: 0; list-style: none; }
.entries li { padding: 8px 0; border-top: 1px dashed var(--line); }
.entries li:first-child { border-top: 0; }
.entry-head .btn { padding: 3px 10px; font-size: 12px; }
</style>
