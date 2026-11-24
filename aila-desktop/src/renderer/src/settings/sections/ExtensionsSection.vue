<script setup lang="ts">
/**
 * 设置 → 扩展：导入扩展包（zip 或文件夹）、看清单再装、开关、填设置、开停工具服务、卸载。
 * 导入时文件由主进程弹窗选、AI 服务检查；这里显示的清单（会加什么、会运行什么程序）是代码从说明书算出来的。
 * 密钥类设置只显示填没填，不回显。
 */
import { t, joinList } from '../../i18n'
import { onActivated, onMounted, ref } from 'vue'
import { summarize, type CatalogListing, type ExtensionInfo, type ExtensionPlan, type ExtensionUpdate } from '../../../../shared/extensions'
import { MCP_STATUS_LABELS } from '../../../../shared/mcp'
import { bridge } from '../../platform/bridge'
import Toggle from '../Toggle.vue'
import McpCard from './McpCard.vue'
import SkillsSection from './SkillsSection.vue'

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
      error.value = res.error ?? t('没成功，重试一下。')
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
  const servers = s.plan.servers.length ? t('它的工具服务先停着，在下面打开。') : ''
  const data = await run('install', () => bridge!.extInstall(s.token), t('装好了「{name}」。{servers}新开的会话里就能用。', { name: s.plan.name, servers }))
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
  await update(e, { settings }, t('设置保存了。开着的工具服务会用新设置重启。'))
}

async function clearSetting(e: ExtensionInfo, key: string): Promise<void> {
  await update(e, { settings: { [key]: '' } }, t('清掉了。'))
}

async function remove(e: ExtensionInfo): Promise<void> {
  if (confirmRemove.value !== e.id) {
    confirmRemove.value = e.id
    return
  }
  confirmRemove.value = ''
  apply(await run(`remove:${e.id}`, () => bridge!.extRemove(e.id), t('卸载了「{name}」，它的工具服务也停了。', { name: e.name })))
}

// ----- 第二、三期：台词、形象、页面 -----

const LINE_NAMES: Record<string, string> = {
  chatOpen: '打开对话', pokeHead: '被戳头', pokeHeadMany: '一直被戳', pokeBody: '被戳身上', dragStart: '被拖起来',
  dragEnd: '被放下', wake: '睡醒', sleepy: '犯困', wakeHere: '被叫醒', forgetOne: '忘掉一件事', agentStart: '开始做任务',
  agentTaskStarted: '任务开始了', testsRecovered: '测试修好了', testsStruggling: '测试一直挂', workMaking: '在写作品', lateNight: '深夜'
}

function linesText(lines: Record<string, number>, replace: boolean): string {
  const parts = Object.entries(lines).map(([k, n]) => t('{what} {n} 句', { what: LINE_NAMES[k] ? t(LINE_NAMES[k]) : k, n }))
  return (replace ? t('整组换掉：') : t('加进去：')) + joinList(parts)
}

function mb(bytes: number): string {
  return bytes >= 1e6 ? `${(bytes / 1e6).toFixed(1)}MB` : `${Math.max(1, Math.round(bytes / 1e3))}KB`
}

function isEmpty(p: ExtensionPlan): boolean {
  return !p.commands.length && !p.skills.length && !p.servers.length && !p.motions.length && !Object.keys(p.lines).length
    && !p.avatars.length && !p.pages.length
}

async function useAvatar(e: ExtensionInfo, name: string): Promise<void> {
  await run(`avatar:${e.id}`, () => bridge!.extUseAvatar(e.id, name), t('换上了「{name}」。想换回来就去「形象」点「换回艾拉自己的形象」。', { name }))
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
  const data = await run('sources', () => bridge!.extCatalogSources(urls), urls.length ? t('目录地址保存了。') : t('目录地址都清掉了。'))
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

/** 拖进来的 zip 或文件夹：路径由预加载脚本从真的拖放文件里取（渲染层自己造不出路径） */
const dropping = ref(false)
async function onDrop(e: DragEvent): Promise<void> {
  dropping.value = false
  const file = e.dataTransfer?.files?.[0]
  if (!file || !bridge) return
  if (staged.value) await discard()
  const data = await run('drop', () => bridge!.extDropFile(file))
  if (data) staged.value = data
}

onMounted(load)
onActivated(load)
</script>

<template>
  <h2>{{ t('技能和扩展') }}</h2>
  <p class="lead">
    {{ t('她会什么都在这一页：导入扩展包（新的命令、做事的本事、外部工具、动作和形象），开关技能包，接外部工具（MCP）。只装你信得过的：装之前会列出它会加什么、会在电脑上运行什么程序。') }}
  </p>

  <div v-if="!bridge" class="offline">{{ t('扩展要在桌面版里管理。') }}</div>

  <template v-else>
    <div class="card drop" :class="{ dropping }" @dragenter.prevent="dropping = true" @dragover.prevent="dropping = true"
      @dragleave.self="dropping = false" @drop.prevent="onDrop">
      <div class="heading">
        <h3>{{ t('导入') }}</h3>
        <div class="actions-inline">
          <button class="btn primary" :disabled="Boolean(busy)" @click="pick('zip')">
            {{ busy === 'pick:zip' ? t('正在检查…') : t('选一个 .zip') }}
          </button>
          <button class="btn" :disabled="Boolean(busy)" @click="pick('folder')">
            {{ busy === 'pick:folder' ? t('正在检查…') : t('选一个文件夹') }}
          </button>
        </div>
      </div>
      <p class="hint">
        {{ t('把 zip 或文件夹') }}<b>{{ t('直接拖到这里') }}</b>{{ t('也行。扩展包是一个带') }} <code>aila-extension.json</code> {{ t('的文件夹（或者压成 zip）；只有一个') }} <code>SKILL.md</code> {{ t('的文件夹（别人分享的一项本事）也能直接导入，会自动包成一个扩展。仓库里的') }} <code>examples/extensions</code> {{ t('有三个例子；怎么写见') }} <code>docs/extensions.md</code>。
      </p>
      <p v-if="dropping" class="drop-hint">{{ t('松开就开始检查') }}</p>

      <div v-if="staged" class="plan" role="region" :aria-label="t('装之前的清单')">
        <div class="plan-head">
          <b>{{ staged.plan.name }}</b>
          <span class="ver">{{ staged.plan.id }} · {{ staged.plan.version }}</span>
          <span v-if="staged.plan.upgrade" class="tag warn">{{ t('升级') }}</span>
        </div>
        <p v-if="staged.plan.description" class="desc">{{ staged.plan.description }}</p>
        <ul class="what">
          <li v-if="staged.plan.commands.length">
            <span class="k">{{ t('命令') }}</span>
            <span><code v-for="c in staged.plan.commands" :key="c" class="chip">/{{ c }}</code></span>
          </li>
          <li v-for="s in staged.plan.skills" :key="s.name">
            <span class="k">{{ t('本事') }}</span><span><b>{{ s.name }}</b>：{{ s.description }}</span>
          </li>
          <li v-for="s in staged.plan.servers" :key="s.name">
            <span class="k">{{ t('会运行') }}</span>
            <span>
              <code class="cmd">{{ s.command }}</code>
              <span v-if="s.readOnly" class="tag good">{{ t('只读工具') }}</span>
              <span v-if="s.env.length" class="env">{{ t('环境变量：') }}{{ joinList(s.env) }}</span>
            </span>
          </li>
          <li v-for="s in staged.plan.settings" :key="s.key">
            <span class="k">{{ t('要填') }}</span>
            <span>{{ s.label }}<template v-if="s.secret">{{ t('（密钥）') }}</template><template v-if="s.required">{{ t('，必填') }}</template></span>
          </li>
          <li v-if="staged.plan.motions.length">
            <span class="k">{{ t('动作') }}</span>
            <span><span v-for="m in staged.plan.motions" :key="m.name" class="chip">[{{ m.name }}] {{ m.label }}</span></span>
          </li>
          <li v-if="Object.keys(staged.plan.lines).length">
            <span class="k">{{ t('台词') }}</span>
            <span>{{ linesText(staged.plan.lines, staged.plan.linesReplace) }}</span>
          </li>
          <li v-if="staged.plan.avatars.length">
            <span class="k">{{ t('形象') }}</span>
            <span>{{ staged.plan.avatars.map((a) => `${a.name}（${a.kind === 'live2d' ? 'Live2D，' : ''}${mb(a.size)}）`).join('、') }}</span>
          </li>
          <li v-if="staged.plan.pages.length">
            <span class="k">{{ t('页面') }}</span>
            <span>{{ staged.plan.pages.map((p) => p.title).join('、') }}</span>
          </li>
          <li v-if="isEmpty(staged.plan)">
            <span class="k"></span><span class="muted">{{ t('里面什么都没有') }}</span>
          </li>
        </ul>
        <p v-for="w in staged.plan.warnings" :key="w" class="msg warn">{{ w }}</p>
        <div class="actions">
          <button class="btn primary" :disabled="Boolean(busy)" @click="install">
            {{ busy === 'install' ? t('正在装…') : staged.plan.upgrade ? t('升级') : t('装') }}
          </button>
          <button class="btn" :disabled="busy === 'install'" @click="discard">{{ t('不装了') }}</button>
        </div>
      </div>

      <p v-if="error" class="msg error" role="alert">{{ error }}</p>
      <p v-if="notice" class="msg ok" role="status">{{ notice }}</p>
    </div>

    <div class="card">
      <div class="heading">
        <h3>{{ t('装好的扩展') }}</h3>
        <button class="btn" :disabled="loading || Boolean(busy)" @click="load">{{ loading ? t('正在读取…') : t('刷新') }}</button>
      </div>
      <p v-if="loading && !items.length" class="empty" role="status">{{ t('正在读取…') }}</p>
      <p v-else-if="!items.length" class="empty">{{ t('还没装扩展。') }}</p>

      <ul class="exts">
        <li v-for="e in items" :key="e.id" class="ext" :class="{ off: !e.enabled }">
          <div class="ext-head">
            <Toggle :model-value="e.enabled" @update:model-value="(v) => update(e, { enabled: v }, v ? t('启用了「{name}」，新开的会话里能用。', { name: e.name }) : t('停用了「{name}」：命令、本事、工具都不给她了。', { name: e.name }))" />
            <b>{{ e.name }}</b>
            <span class="ver">{{ e.id }} · {{ e.version }}</span>
            <span class="sum">{{ summarize(e) }}</span>
            <span class="spacer"></span>
            <button class="link" @click="open[e.id] = !open[e.id]">{{ open[e.id] ? t('收起') : t('详细') }}</button>
            <button class="btn danger" :disabled="Boolean(busy)" @click="remove(e)">
              {{ confirmRemove === e.id ? t('再点一次卸载') : t('卸载') }}
            </button>
          </div>
          <p v-if="e.description" class="desc">{{ e.description }}</p>
          <p v-if="e.missing.length" class="msg warn">{{ t('还没填：{list}（填了才能打开它的工具服务）', { list: joinList(e.missing) }) }}</p>

          <div v-if="open[e.id]" class="detail">
            <div v-if="e.commands.length" class="block">
              <span class="k">{{ t('命令') }}</span>
              <span><code v-for="c in e.commands" :key="c" class="chip">/{{ c }}</code></span>
            </div>
            <div v-for="s in e.skills" :key="s.name" class="block">
              <span class="k">{{ t('本事') }}</span><span><b>{{ s.name }}</b>：{{ s.description }}</span>
            </div>
            <div v-if="e.motions.length" class="block">
              <span class="k">{{ t('动作') }}</span>
              <span>
                <span v-for="m in e.motions" :key="m.name" class="chip">
                  [{{ m.name }}] {{ m.label }}
                  <button class="link" :disabled="!e.enabled" :title="t('让桌面上的她做一遍')" @click="bridge!.extPlay(m.name)">{{ t('试一下') }}</button>
                </span>
                <span class="hint">{{ t('对话里她也会用（写成 [{name}] 这样的标签）', { name: e.motions[0].name }) }}</span>
              </span>
            </div>
            <div v-if="Object.keys(e.lines).length" class="block">
              <span class="k">{{ t('台词') }}</span><span>{{ linesText(e.lines, e.linesReplace) }}</span>
            </div>
            <div v-for="a in e.avatars" :key="a.name" class="block">
              <span class="k">{{ t('形象') }}</span>
              <span>
                {{ a.name }}（{{ a.kind === 'live2d' ? 'Live2D，' : '' }}{{ mb(a.size) }}）
                <button class="link" :disabled="!e.enabled || Boolean(busy)" @click="useAvatar(e, a.name)">{{ t('换上') }}</button>
              </span>
            </div>
            <div v-for="p in e.pages" :key="p.name" class="block">
              <span class="k">{{ t('页面') }}</span>
              <span>
                {{ p.title }}
                <button class="link" :disabled="!e.enabled" @click="openPage(e, p.name, p.title)">{{ t('打开') }}</button>
              </span>
            </div>

            <div v-for="s in e.servers" :key="s.name" class="server">
              <div class="server-head">
                <i class="dot" :class="s.status" aria-hidden="true"></i>
                <b>{{ s.name }}</b>
                <span class="state" :class="s.status">{{ MCP_STATUS_LABELS[s.status] ?? s.status }}</span>
                <span v-if="s.readOnly" class="tag good">{{ t('只读工具') }}</span>
                <span class="spacer"></span>
                <button class="btn" :disabled="Boolean(busy) || !e.enabled || (s.disabled && e.missing.length > 0)"
                  @click="update(e, { servers: { [s.name]: !s.disabled } }, s.disabled ? t('打开了 {name}。', { name: s.name }) : t('停了 {name}，她不会再看到它的工具。', { name: s.name }))">
                  {{ busy === `update:${e.id}` ? '…' : s.disabled ? t('打开') : t('停用') }}
                </button>
              </div>
              <code v-if="s.command" class="cmd">{{ s.command }}</code>
              <p v-if="s.error" class="msg error">{{ s.error }}</p>
              <ul v-if="s.tools.length" class="tools">
                <li v-for="tool in s.tools" :key="tool.name">
                  <code>{{ tool.name }}</code>
                  <span v-if="tool.readOnly" class="tag good">{{ t('只读，不问') }}</span>
                  <span v-else-if="tool.alwaysAllow" class="tag">{{ t('以后都不问') }}</span>
                  <span v-else class="tag">{{ t('每次问') }}</span>
                  <span class="desc">{{ tool.description }}</span>
                </li>
              </ul>
              <details v-if="s.log.length" class="log">
                <summary>{{ t('最近的输出') }}</summary>
                <pre>{{ s.log.join('\n') }}</pre>
              </details>
            </div>

            <div v-if="e.settings.length" class="settings">
              <div v-for="s in e.settings" :key="s.key" class="row">
                <span class="label">{{ s.label }}<template v-if="s.required"> *</template></span>
                <div>
                  <input v-model="drafts[e.id][s.key]" :type="s.secret ? 'password' : 'text'" autocomplete="off"
                    :placeholder="s.secret && s.set ? t('已经填了（不显示），要换就填新的') : ''" />
                  <button v-if="s.secret && s.set" class="link" @click="clearSetting(e, s.key)">{{ t('清掉') }}</button>
                  <span v-if="s.description" class="hint">{{ s.description }}</span>
                </div>
              </div>
              <div class="actions">
                <button class="btn" :disabled="Boolean(busy)" @click="saveSettings(e)">{{ t('保存设置') }}</button>
              </div>
            </div>
          </div>
        </li>
      </ul>
      <p v-if="folder" class="hint where">{{ t('装在') }} <code>{{ folder }}</code></p>
    </div>

    <div class="card">
      <div class="heading">
        <h3>{{ t('目录') }}</h3>
        <button class="btn" :disabled="catalogLoading || Boolean(busy)" @click="loadCatalog">
          {{ catalogLoading ? t('正在读取…') : catalogs.length ? t('刷新') : t('看看目录') }}
        </button>
      </div>
      <p class="hint">
        {{ t('目录是一份') }} <code>catalog.json</code>{{ t('，列着能装的扩展。地址可以是 https 网址，也可以是这台电脑上的完整路径，一行一个。从目录装和导入一样：下载、核对 sha256、先给你看清单。现在还没有官方目录；仓库里的') }}
        <code>examples/extensions/make_catalog.py</code> {{ t('能生成一份本机目录试试。') }}
      </p>
      <textarea v-model="sourcesText" rows="3" class="sources" placeholder="https://example.com/aila/catalog.json&#10;D:\aila-extensions\catalog.json"></textarea>
      <div class="actions">
        <button class="btn" :disabled="Boolean(busy)" @click="saveSources">{{ busy === 'sources' ? t('正在保存…') : t('保存目录地址') }}</button>
      </div>
      <div v-for="c in catalogs" :key="c.source" class="catalog">
        <div class="catalog-head"><b>{{ c.name }}</b><span v-if="c.name !== c.source" class="ver">{{ c.source }}</span></div>
        <p v-if="c.error" class="msg error">{{ c.error }}</p>
        <p v-else-if="!c.extensions.length" class="empty">{{ t('这个目录里没有扩展。') }}</p>
        <ul class="entries">
          <li v-for="x in c.extensions" :key="x.id">
            <div class="entry-head">
              <b>{{ x.name }}</b>
              <span class="ver">{{ x.id }} · {{ x.version }}<template v-if="x.size"> · {{ mb(x.size) }}</template></span>
              <span v-if="x.installed && !x.upgrade" class="tag good">{{ t('装了') }}</span>
              <span v-else-if="x.upgrade" class="tag warn">{{ t('有新版（装的是 {v}）', { v: x.installed ?? '' }) }}</span>
              <span class="spacer"></span>
              <button class="btn" :disabled="Boolean(busy) || Boolean(x.installed && !x.upgrade)" @click="fromCatalog(c.source, x.id)">
                {{ busy === `catalog:${x.id}` ? t('正在下载…') : x.upgrade ? t('看看新版') : t('看看') }}
              </button>
            </div>
            <p v-if="x.description" class="desc">{{ x.description }}</p>
          </li>
        </ul>
      </div>
    </div>

    <SkillsSection embedded />

    <McpCard />
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
.drop { transition: border-color 0.15s, background 0.15s; }
.drop.dropping { border-color: var(--accent); background: var(--accent-soft); }
.drop-hint { margin: 6px 0 0; color: var(--accent); font-weight: 600; }
.section-title { margin: 26px 0 8px; }
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
