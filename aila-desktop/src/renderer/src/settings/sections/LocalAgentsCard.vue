<script setup lang="ts">
/**
 * 设置 → 编程 →「本机 Agent」：这台电脑上的 Codex、Claude Code、Antigravity 能不能交活。
 * 艾拉自己去找（PATH 里的程序、它们自己的登录状态），登录要你在终端里用它们的官方程序做，艾拉不经手账号。
 * 和它们说话要一个 ACP 适配器（Node 程序）：点「装适配器」才下载，不带它们自带的 Codex / Claude，用你装好的。
 */
import { t } from '../../i18n'
import { onMounted, ref } from 'vue'
import type { LocalAgentInfo } from '../../../../shared/agent'
import { bridge } from '../../platform/bridge'

const agents = ref<LocalAgentInfo[]>([])
const checking = ref(false)
const installing = ref(false)
const error = ref('')
const notice = ref('')

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
      {{ t('开新任务时可以交给这台电脑上的 Codex、Claude Code 或 Antigravity 做：用你自己的账号和额度，进度、改动、要你同意的操作都在任务页里。它们要改文件、跑命令时照样按艾拉的权限模式问你，危险命令总是问。') }}
    </p>
    <ul class="list">
      <li v-for="a in agents" :key="a.id" :class="{ off: !a.ready }">
        <div class="main">
          <b>{{ a.title }}</b>
          <span class="hint">
            <template v-if="a.path">{{ a.version || a.path }}</template>
            <template v-else>{{ t('没找到') }}</template>
            <template v-if="a.logged_in === true"> · {{ t('已登录') }}</template>
            <template v-else-if="a.logged_in === false"> · {{ t('没登录') }}</template>
            <template v-if="a.path && a.native"> · {{ t('自己就支持 ACP，不用适配器') }}</template>
            <template v-else-if="a.path"> · {{ a.adapter ? t('适配器装好了') : t('还没装适配器') }}</template>
          </span>
          <span v-if="a.problem" class="problem">{{ a.problem }}<template v-for="h in a.hints" :key="h">：{{ h }}</template></span>
        </div>
        <span class="tag" :class="a.ready ? 'good' : 'warn'">{{ a.ready ? t('能用') : t('还不能用') }}</span>
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

.row {
  display: flex;
  gap: 8px;
  margin-top: 6px;
}
</style>
