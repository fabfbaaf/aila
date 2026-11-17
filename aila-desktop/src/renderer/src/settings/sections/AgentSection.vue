<script setup lang="ts">
/** 编程权限说明、记住的命令（只能撤销授权，删除后立即按原有权限规则判断）、外部工具（MCP）。 */
import { computed, onActivated, ref } from 'vue'
import { MODE_HINTS, MODE_LABELS, type AgentMode } from '../../../../shared/agent'
import type { AgentRuleGroup } from '../../../../shared/settings'
import { loadAgentRules, removeAgentRule } from '../agentRules'
import McpCard from './McpCard.vue'

const modes: AgentMode[] = ['read_only', 'ask', 'auto']
const groups = ref<AgentRuleGroup[]>([])
const projects = computed(() => groups.value.filter((group) => group.commands.length))
const loading = ref(false)
const removing = ref('')
const error = ref('')
const notice = ref('')

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    const res = await loadAgentRules()
    if (res.ok) groups.value = res.data ?? []
    else error.value = res.error ?? '没能读取记住的命令，稍后重试。'
  } catch {
    error.value = '没能读取记住的命令，稍后重试。'
  } finally {
    loading.value = false
  }
}

async function remove(project: string, command: string): Promise<void> {
  removing.value = JSON.stringify([project, command])
  error.value = ''
  notice.value = ''
  try {
    const res = await removeAgentRule(project, command)
    if (!res.ok) {
      error.value = res.error ?? '没能删掉这条命令，重试一下。'
      return
    }
    groups.value = groups.value.map((group) => group.project === project
      ? { ...group, commands: group.commands.filter((item) => item !== command) } : group)
    notice.value = '删掉了这条记住的命令。下次执行时按当前权限模式判断。'
  } catch {
    error.value = '没能删掉这条命令，重试一下。'
  } finally {
    removing.value = ''
  }
}

onActivated(load)
</script>

<template>
  <section class="agent-settings">
    <h2>编程</h2>
    <p class="lead">每个任务可以单独切换权限模式。这里能查看和撤销你选过的「以后都允许」。</p>

    <div class="card">
      <h3>三种权限模式</h3>
      <dl class="modes">
        <div v-for="mode in modes" :key="mode">
          <dt>{{ MODE_LABELS[mode] }}</dt>
          <dd>{{ MODE_HINTS[mode] }}</dd>
        </div>
      </dl>
      <p class="safety">危险命令不管哪种模式都要问，也不能「以后都允许」。只读模式下会拒绝执行。读可能存着密钥的文件也要问。</p>
    </div>

    <div class="card" :aria-busy="loading || Boolean(removing)">
      <div class="rule-heading">
        <h3>记住的命令</h3>
        <button class="btn" :disabled="loading || Boolean(removing)" @click="load">{{ loading ? '正在读取…' : '刷新' }}</button>
      </div>
      <p class="rule-note">只在对应项目里按原文匹配。删掉后立即生效，不会停止正在跑的命令。</p>
      <p v-if="error" class="msg error" role="alert">{{ error }}</p>
      <p v-if="notice" class="msg ok" role="status">{{ notice }}</p>
      <p v-if="loading && !groups.length" class="empty" role="status">正在读取记住的命令…</p>
      <p v-else-if="!projects.length && !error" class="empty">还没有记住的命令。确认命令时选「以后都允许」，才会出现在这里。</p>
      <section v-for="group in projects" :key="group.project" class="project">
        <h4 class="project-path">{{ group.project }}</h4>
        <ul class="commands">
          <li v-for="command in group.commands" :key="command">
            <code>{{ command }}</code>
            <button class="btn danger" :disabled="loading || Boolean(removing)" :aria-label="`删掉 ${group.project} 记住的命令：${command}`" @click="remove(group.project, command)">
              {{ removing === JSON.stringify([group.project, command]) ? '正在删…' : '删掉' }}
            </button>
          </li>
        </ul>
      </section>
    </div>

    <McpCard />
  </section>
</template>

<style scoped>
.agent-settings { line-height: 1.6; }
.modes { margin: 0; }
.modes > div { display: grid; grid-template-columns: 112px 1fr; gap: 12px; padding: 8px 0; }
.modes dt { font-weight: 600; }
.modes dd { margin: 0; color: var(--label); }
.safety { margin: 12px 0 0; padding-top: 12px; border-top: 1px solid var(--line); color: var(--label); }
.rule-heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.rule-heading h3 { margin-bottom: 0; }
.rule-note { margin: 10px 0 14px; color: var(--label); }
.project + .project { margin-top: 22px; }
.project-path { margin: 0 0 4px; color: var(--label); font-size: 13px; font-weight: 600; overflow-wrap: anywhere; }
.commands { margin: 0; padding: 0; list-style: none; }
.commands li { display: flex; gap: 16px; align-items: start; padding: 10px 0; border-bottom: 1px solid var(--line); }
.commands li:last-child { border-bottom: 0; }
.commands code { flex: 1; min-width: 0; white-space: pre-wrap; overflow-wrap: anywhere; padding-top: 6px; font: 13px/1.6 Consolas, monospace; }
.commands .btn { flex-shrink: 0; min-height: 34px; }
button:focus-visible { outline: 2px solid var(--focus); outline-offset: 3px; }
@media (max-width: 700px) {
  .modes > div { grid-template-columns: 1fr; gap: 0; }
  .commands li { gap: 8px; }
}
</style>
