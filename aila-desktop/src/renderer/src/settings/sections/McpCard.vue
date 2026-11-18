<script setup lang="ts">
/**
 * 设置页 → 编程 → 外部工具（MCP）：加、看、重启、停用、删除 MCP 服务，撤销「这个工具以后都不用问」。
 * 加服务 = 让这台电脑跑一个程序：先把贴进来的配置给 AI 服务解析，显示要启动的命令（代码拼的），你点了才保存启动。
 * 服务的环境变量（常常是密钥）只显示名字。
 */
import { onActivated, onMounted, ref } from 'vue'
import { MCP_STATUS_LABELS, type McpPreviewItem, type McpServerInfo } from '../../../../shared/mcp'
import { MCP_EXAMPLE, disableMcp, forgetMcpTool, importMcp, loadMcp, previewMcp, removeMcp, restartMcp } from '../mcp'

const servers = ref<McpServerInfo[]>([])
const loading = ref(false)
const busy = ref('')
const error = ref('')
const notice = ref('')
const open = ref<Record<string, boolean>>({})
const confirmRemove = ref('')

const adding = ref(false)
const paste = ref('')
const preview = ref<McpPreviewItem[] | null>(null)

async function run(label: string, action: () => Promise<{ ok: boolean; data?: McpServerInfo[]; error?: string }>, done = ''): Promise<boolean> {
  busy.value = label
  error.value = ''
  notice.value = ''
  try {
    const res = await action()
    if (!res.ok) {
      error.value = res.error ?? '没成功，重试一下。'
      return false
    }
    if (res.data) servers.value = res.data
    notice.value = done
    return true
  } finally {
    busy.value = ''
  }
}

async function load(): Promise<void> {
  loading.value = true
  await run('', loadMcp)
  loading.value = false
}

async function check(): Promise<void> {
  preview.value = null
  busy.value = 'preview'
  error.value = ''
  try {
    const res = await previewMcp(paste.value)
    if (res.ok) preview.value = res.data ?? []
    else error.value = res.error ?? '配置不对。'
  } finally {
    busy.value = ''
  }
}

async function save(): Promise<void> {
  const ok = await run('import', () => importMcp(paste.value), '保存了。启动好的服务，她从下一轮开始就能用。')
  if (ok) {
    adding.value = false
    paste.value = ''
    preview.value = null
  }
}

function cancelAdd(): void {
  adding.value = false
  paste.value = ''
  preview.value = null
}

async function remove(name: string): Promise<void> {
  if (confirmRemove.value !== name) {
    confirmRemove.value = name
    return
  }
  confirmRemove.value = ''
  await run(`remove:${name}`, () => removeMcp(name), `删掉了 ${name}，它的进程也停了。`)
}

onMounted(load)
onActivated(load)
</script>

<template>
  <div class="card mcp" :aria-busy="loading || Boolean(busy)">
    <div class="mcp-heading">
      <h3>外部工具（MCP）</h3>
      <div class="actions-inline">
        <button class="btn" :disabled="loading || Boolean(busy)" @click="load">{{ loading ? '正在读取…' : '刷新' }}</button>
        <button v-if="!adding" class="btn primary" :disabled="Boolean(busy)" @click="adding = true">加一个服务</button>
      </div>
    </div>
    <p class="mcp-note">
      MCP 服务是别人写好的工具程序（数据库、浏览器、GitHub……），装上以后她做任务时能用。每个服务都是这台电脑上跑的一个程序，只加你信得过的。
      服务标了只读的工具她直接用；其他的每次都先问你，可以选「这个工具以后都不用问」。
      配置和对话共用 aila-ai/mcp.json：这里加的服务，对话里（右键菜单的技能包）也会自动连上。
    </p>

    <p v-if="error" class="msg error" role="alert">{{ error }}</p>
    <p v-if="notice" class="msg ok" role="status">{{ notice }}</p>

    <div v-if="adding" class="add">
      <label for="mcp-paste">贴服务说明里的配置（JSON，和 Claude Desktop 的 mcpServers 写法一样）</label>
      <textarea id="mcp-paste" v-model="paste" rows="8" spellcheck="false" :placeholder="MCP_EXAMPLE" @input="preview = null"></textarea>
      <div v-if="preview" class="preview">
        <p>保存后会在这台电脑上启动：</p>
        <ul>
          <li v-for="p in preview" :key="p.name">
            <b>{{ p.name }}</b>
            <span v-if="p.exists" class="tag warn">会覆盖原来的</span>
            <span v-if="!p.supported" class="tag warn">远程服务暂不支持</span>
            <code>{{ p.command }}</code>
            <span v-if="p.envKeys.length" class="env">环境变量：{{ p.envKeys.join('、') }}（值不显示）</span>
          </li>
        </ul>
      </div>
      <div class="actions">
        <button v-if="!preview" class="btn primary" :disabled="paste.trim().length < 2 || Boolean(busy)" @click="check">
          {{ busy === 'preview' ? '正在检查…' : '检查' }}
        </button>
        <button v-else class="btn primary" :disabled="Boolean(busy)" @click="save">
          {{ busy === 'import' ? '正在启动…（第一次用 npx 要下载，可能要一会儿）' : '保存并启动' }}
        </button>
        <button class="btn" :disabled="busy === 'import'" @click="cancelAdd">取消</button>
      </div>
    </div>

    <p v-if="loading && !servers.length" class="empty" role="status">正在读取…</p>
    <p v-else-if="!servers.length && !adding" class="empty">还没有 MCP 服务。点「加一个服务」，贴服务说明里的配置。</p>

    <ul class="servers">
      <li v-for="s in servers" :key="s.name" class="server">
        <div class="server-head">
          <i class="dot" :class="s.status" aria-hidden="true"></i>
          <b>{{ s.label || s.name }}</b>
          <span v-if="s.label && s.label !== s.name" class="ver">{{ s.name }}</span>
          <span v-if="s.readOnly" class="tag good">整个服务只读</span>
          <span class="state" :class="s.status">{{ MCP_STATUS_LABELS[s.status] ?? s.status }}</span>
          <span v-if="s.server?.name" class="ver">{{ s.server.name }} {{ s.server.version }}</span>
          <button v-if="s.tools.length" class="link toggle" @click="open[s.name] = !open[s.name]">
            {{ s.tools.length }} 个工具 {{ open[s.name] ? '▴' : '▾' }}
          </button>
          <span class="spacer"></span>
          <button class="btn" :disabled="Boolean(busy) || s.disabled" @click="run(`restart:${s.name}`, () => restartMcp(s.name), `重启了 ${s.name}。`)">
            {{ busy === `restart:${s.name}` ? '正在重启…' : '重启' }}
          </button>
          <button class="btn" :disabled="Boolean(busy)" @click="run(`disable:${s.name}`, () => disableMcp(s.name, !s.disabled), s.disabled ? `启用了 ${s.name}。` : `停用了 ${s.name}，她不会再看到它的工具。`)">
            {{ s.disabled ? '启用' : '停用' }}
          </button>
          <button class="btn danger" :disabled="Boolean(busy)" @click="remove(s.name)">
            {{ confirmRemove === s.name ? '确认删除' : '删除' }}
          </button>
        </div>
        <code class="cmd">{{ s.command }}</code>
        <div v-if="s.envKeys.length" class="env">环境变量：{{ s.envKeys.join('、') }}</div>
        <p v-if="s.error" class="msg error">{{ s.error }}</p>
        <ul v-if="open[s.name]" class="tools">
          <li v-for="t in s.tools" :key="t.name">
            <code>{{ t.name }}</code>
            <span v-if="t.readOnly" class="tag good">只读，不问</span>
            <span v-else-if="t.alwaysAllow" class="tag">以后都不问</span>
            <button v-if="t.alwaysAllow" class="link" :disabled="Boolean(busy)" @click="run(`forget:${s.name}`, () => forgetMcpTool(s.name, t.name), `${t.name} 下次用时会先问你。`)">撤销</button>
            <span class="desc">{{ t.description }}</span>
          </li>
        </ul>
        <details v-if="s.log.length && s.status !== 'ready'" class="log">
          <summary>最近的日志</summary>
          <pre>{{ s.log.join('\n') }}</pre>
        </details>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.mcp-heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.mcp-heading h3 { margin-bottom: 0; }
.actions-inline { display: flex; gap: 8px; }
.mcp-note { margin: 10px 0 14px; color: var(--label); }
.add { margin: 4px 0 16px; padding: 14px; border: 1px dashed var(--field-line); border-radius: var(--ui-r, 9px); }
.add label { display: block; margin-bottom: 8px; font-weight: 600; }
.add textarea {
  box-sizing: border-box;
  width: 100%;
  padding: 10px 12px;
  border: 1px solid var(--field-line);
  border-radius: var(--ui-r-s, 6px);
  background: var(--bg);
  color: var(--text);
  font: 12.5px/1.55 var(--ui-mono, Consolas, monospace);
  resize: vertical;
  outline: none;
}
.add textarea:focus { border-color: var(--focus); box-shadow: 0 0 0 3px var(--accent-soft); }
.preview { margin-top: 12px; }
.preview p { margin: 0 0 6px; color: var(--label); }
.preview ul { margin: 0; padding: 0; list-style: none; }
.preview li { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 10px; padding: 6px 0; border-bottom: 1px solid var(--line); }
.preview li:last-child { border-bottom: 0; }
.preview code, .cmd { font: 12px/1.5 var(--ui-mono, Consolas, monospace); color: var(--ui-cyan, #67e8f9); overflow-wrap: anywhere; }
.preview code { flex-basis: 100%; }
.env { color: var(--muted); font-size: 12px; }
.servers { margin: 0; padding: 0; list-style: none; }
.server { padding: 12px 0; border-top: 1px solid var(--line); }
.server:first-child { border-top: 0; }
.server-head { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.server-head .btn { padding: 3px 10px; font-size: 12px; }
.spacer { flex: 1; }
.dot { width: 8px; height: 8px; border-radius: 50%; background: var(--x); }
.dot.ready { background: var(--ok); box-shadow: 0 0 0 3px var(--good-bg); }
.dot.starting { background: var(--accent); animation: pulse 1s infinite; }
.dot.failed, .dot.unsupported { background: var(--danger); }
@keyframes pulse { 50% { opacity: 0.35; } }
.state { font-size: 12px; color: var(--muted); }
.state.ready { color: var(--ok); }
.state.failed, .state.unsupported { color: var(--danger); }
.ver { font-size: 12px; color: var(--muted); }
.toggle { font-size: 12.5px; }
.cmd { display: block; margin-top: 6px; }
.tools { margin: 8px 0 0; padding: 8px 12px; list-style: none; border-radius: var(--ui-r-s, 6px); background: var(--bg); }
.tools li { display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px 8px; padding: 4px 0; }
.tools code { font: 12px var(--ui-mono, Consolas, monospace); }
.tools .desc { flex-basis: 100%; color: var(--muted); font-size: 12px; }
.log summary { margin-top: 6px; color: var(--muted); font-size: 12px; cursor: pointer; }
.log pre { max-height: 180px; overflow: auto; margin: 6px 0 0; padding: 8px 10px; border-radius: var(--ui-r-s, 6px); background: var(--bg); font: 11.5px/1.5 var(--ui-mono, Consolas, monospace); white-space: pre-wrap; }
</style>
