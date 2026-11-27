<script setup lang="ts">
/**
 * 设置 → 编程 →「本机 Agent」：检测已安装的命令行工具，并给未检测到的工具显示官方安装地址。
 */
import { t } from '../../i18n'
import { computed, onMounted, ref } from 'vue'
import type { AgentEngine, LocalAgentInfo } from '../../../../shared/agent'
import { agentInstallUrl } from '../../../../shared/agentInstall'
import { bridge } from '../../platform/bridge'

const agents = ref<LocalAgentInfo[]>([])
const checking = ref(false)
const installing = ref(false)
const error = ref('')
const notice = ref('')
const accountAgents = computed(() => agents.value.filter((agent) => !agent.id.endsWith('-alt') || agent.path))
const hasAccountSlots = computed(() => accountAgents.value.some((agent) => agent.id.endsWith('-alt')))

async function check(): Promise<void> {
  if (!bridge || checking.value) return
  checking.value = true
  error.value = ''
  try {
    const r = await bridge.agentLocal()
    if (r.ok && r.data) agents.value = r.data
    else error.value = r.error ?? t('没问到，AI 服务可能没开。')
  } finally {
    checking.value = false
  }
}

async function openInstall(id: AgentEngine): Promise<void> {
  if (!bridge) return
  error.value = ''
  try {
    const r = await bridge.agentLocalOpenInstall(id)
    if (!r.ok) error.value = r.error ?? t('没打开官方安装页。')
  } catch {
    error.value = t('没打开官方安装页。')
  }
}

async function login(id: AgentEngine): Promise<void> {
  if (!bridge) return
  error.value = ''
  try {
    const r = await bridge.agentLocalLogin(id)
    if (r.ok) notice.value = t('在打开的窗口里登录，回来点「重新检查」。')
    else error.value = r.error ?? t('登录窗口没打开')
  } catch {
    error.value = t('登录窗口没打开')
  }
}

async function install(): Promise<void> {
  if (!bridge || installing.value) return
  installing.value = true
  error.value = ''
  notice.value = ''
  try {
    const r = await bridge.agentLocalInstall()
    if (r.ok && r.data) {
      notice.value = r.data.message
      agents.value = r.data.agents
    } else error.value = r.error ?? t('没装上。')
  } finally {
    installing.value = false
  }
}

const needsAdapter = (): boolean => agents.value.some((a) => a.path && !a.native && !a.adapter)

onMounted(check)
</script>

<template>
  <div class="card" :aria-busy="checking || installing">
    <h3>{{ t('本机 Agent') }}</h3>
    <p class="hint">
      {{ t('开新任务时可选择本机的 Agent。登录和模型配置在各自的命令行工具里完成，任务进度和权限请求在艾拉里查看。') }}
    </p>
    <p v-if="hasAccountSlots" class="hint">{{ t('Codex 和 Claude Code 可以分别登录两个账号。选模型时选带「2」的那一组，就用第二个账号。') }}</p>
    <ul class="list">
      <li v-for="a in accountAgents" :key="a.id" :class="{ off: !a.ready }">
        <div class="main">
          <b>{{ a.title }}</b>
          <span v-if="a.path" class="hint">
            {{ t('已安装') }} · {{ a.version || a.path }}
            <template v-if="a.logged_in === true"> · {{ t('已登录') }}</template>
            <template v-else-if="a.logged_in === false"> · {{ t('没登录') }}</template>
            <template v-else-if="a.native"> · {{ t('登录状态待确认') }}</template>
            <template v-if="a.native"> · {{ t('自己就支持 ACP，不用适配器') }}</template>
            <template v-else> · {{ a.adapter ? t('适配器装好了') : a.installs_adapter ? t('适配器第一次用时自动装') : t('还没装适配器') }}</template>
          </span>
          <span v-else class="hint">{{ t('未检测到') }}</span>
          <span v-if="a.path && (a.problem || a.hints.length)" :class="a.problem ? 'problem' : 'hint'">
            {{ [a.problem, ...a.hints].filter(Boolean).join(' · ') }}
          </span>
        </div>
        <button v-if="a.can_login && a.logged_in !== true" class="btn" type="button"
          :aria-label="t('登录 {name}', { name: a.title })" @click="login(a.id)">{{ a.id.endsWith('-alt') ? t('登录第二个账号') : t('登录') }}</button>
        <span v-if="a.path" class="tag" :class="a.ready ? 'good' : 'warn'">{{ a.ready ? t('能用') : t('还不能用') }}</span>
        <a v-else-if="agentInstallUrl(a.id)"
          class="btn install-link"
          :href="agentInstallUrl(a.id)"
          :aria-label="t('打开 {name} 的官方安装说明', { name: a.title })"
          @click.prevent="openInstall(a.id)"
        >{{ t('官方安装') }}</a>
      </li>
    </ul>
    <p v-if="checking && !agents.length" class="hint">{{ t('正在找本机的编程 Agent…') }}</p>
    <div class="row">
      <button class="btn" :disabled="checking" @click="check">{{ checking ? t('正在检查…') : t('重新检查') }}</button>
      <button v-if="needsAdapter()" class="btn primary" :disabled="installing" @click="install">
        {{ installing ? t('正在装适配器…（几十秒）') : t('装适配器') }}
      </button>
    </div>
    <p v-if="needsAdapter()" class="hint">{{ t('适配器从 npm 下载（两个，共几十 MB，要装了 Node.js），装在艾拉的数据文件夹里。') }}</p>
    <p v-if="notice" class="msg ok">{{ notice }}</p>
    <p v-if="error" class="msg error" role="alert">{{ error }}</p>
  </div>
</template>

<style scoped>
.list {
  margin: 8px 0;
  padding: 0;
  list-style: none;
}

.list li {
  display: flex;
  gap: 12px;
  align-items: flex-start;
  padding: 8px 0;
  border-top: 1px solid var(--ui-line, rgba(255, 255, 255, 0.08));
}

.list li.off .main b {
  opacity: 0.75;
}

.main {
  display: flex;
  flex-direction: column;
  gap: 2px;
  flex: 1;
  min-width: 0;
}

.main .hint {
  margin: 0;
  overflow-wrap: anywhere;
}

.problem {
  color: var(--warn, #d97706);
  font-size: 12.5px;
}

.install-link {
  flex-shrink: 0;
  text-decoration: none;
}

.row {
  display: flex;
  gap: 8px;
  margin-top: 6px;
}
</style>
