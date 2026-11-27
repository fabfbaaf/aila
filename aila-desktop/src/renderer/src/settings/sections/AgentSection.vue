<script setup lang="ts">
/** 编程权限说明、记住的命令、电脑任务的环境说明预览和外部工具（MCP）。 */
import { t, localized } from '../../i18n'
import { computed, onActivated, ref } from 'vue'
import { MODE_HINTS as MODE_HINTS_RAW, MODE_LABELS as MODE_LABELS_RAW, type AgentMode } from '../../../../shared/agent'
const MODE_LABELS = localized(MODE_LABELS_RAW)
const MODE_HINTS = localized(MODE_HINTS_RAW)
import type { AgentRuleGroup } from '../../../../shared/settings'
import { loadAgentRules, removeAgentRule } from '../agentRules'
import { loadComputerContext, loadComputerSettings, saveComputerInventory } from '../computer'
import { bridge } from '../../platform/bridge'
import SchedulesCard from './SchedulesCard.vue'
import LocalAgentsCard from './LocalAgentsCard.vue'
import ProviderQuotasCard from './ProviderQuotasCard.vue'

const modes: AgentMode[] = ['read_only', 'ask', 'auto', 'full']
const groups = ref<AgentRuleGroup[]>([])
const projects = computed(() => groups.value.filter((group) => group.commands.length))
const loading = ref(false)
const removing = ref('')
const error = ref('')
const notice = ref('')
const inventory = ref(true)
const inventoryReady = ref(false)
const inventoryBusy = ref(false)
const computerError = ref('')
const previewError = ref('')
const computerNotice = ref('')
const previewOpen = ref(false)
const previewLoading = ref(false)
const computerContext = ref('')
let previewRequest = 0

async function loadComputer(): Promise<void> {
  inventoryBusy.value = true
  computerError.value = ''
  try {
    const res = await loadComputerSettings()
    if (!res.ok || !res.data) {
      computerError.value = res.error ?? t('没能读取电脑任务设置，重试一下。')
      return
    }
    inventory.value = res.data.computer?.inventory ?? true
    inventoryReady.value = true
    if (previewOpen.value) void preview()
  } catch {
    computerError.value = t('没能读取电脑任务设置，重试一下。')
  } finally {
    inventoryBusy.value = false
  }
}

async function preview(): Promise<void> {
  const request = ++previewRequest
  previewLoading.value = true
  previewError.value = ''
  computerContext.value = ''
  try {
    const res = await loadComputerContext()
    if (request !== previewRequest) return
    if (res.ok && res.data) computerContext.value = res.data.context
    else previewError.value = res.error ?? t('没能读取环境说明，重试一下。')
  } catch {
    if (request === previewRequest) previewError.value = t('没能读取环境说明，重试一下。')
  } finally {
    if (request === previewRequest) previewLoading.value = false
  }
}

function togglePreview(): void {
  previewOpen.value = !previewOpen.value
  if (previewOpen.value) void preview()
  else {
    previewRequest++
    previewLoading.value = false
  }
}

async function saveInventory(): Promise<void> {
  const next = inventory.value
  inventoryBusy.value = true
  computerError.value = ''
  computerNotice.value = ''
  previewRequest++
  computerContext.value = ''
  try {
    const res = await saveComputerInventory(next)
    if (!res.ok || !res.data) {
      inventory.value = !next
      computerError.value = res.error ?? t('没有保存，重试一下。')
      return
    }
    inventory.value = res.data.computer?.inventory ?? next
    computerNotice.value = t('已保存，下次新建电脑任务时使用。已有任务的环境说明保持原样。')
  } catch {
    inventory.value = !next
    computerError.value = t('没有保存，重试一下。')
  } finally {
    inventoryBusy.value = false
    if (previewOpen.value) void preview()
  }
}

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    const res = await loadAgentRules()
    if (res.ok) groups.value = res.data ?? []
    else error.value = res.error ?? t('没能读取记住的命令，稍后重试。')
  } catch {
    error.value = t('没能读取记住的命令，稍后重试。')
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
      error.value = res.error ?? t('没能删掉这条命令，重试一下。')
      return
    }
    groups.value = groups.value.map((group) => group.project === project
      ? { ...group, commands: group.commands.filter((item) => item !== command) } : group)
    notice.value = t('删掉了这条记住的命令。下次执行时按当前权限模式判断。')
  } catch {
    error.value = t('没能删掉这条命令，重试一下。')
  } finally {
    removing.value = ''
  }
}

onActivated(load)
onActivated(loadComputer)
</script>

<template>
  <section class="agent-settings">
    <h2>{{ t('编程') }}</h2>
    <p class="lead">{{ t('每个任务可以单独切换权限模式。这里能查看和撤销你选过的「以后都允许」。') }}</p>

    <ProviderQuotasCard v-if="bridge" />

    <div class="card">
      <h3>{{ t('四种权限模式') }}</h3>
      <dl class="modes">
        <div v-for="mode in modes" :key="mode">
          <dt>{{ MODE_LABELS[mode] }}</dt>
          <dd>{{ MODE_HINTS[mode] }}</dd>
        </div>
      </dl>
      <p class="safety">{{ t('危险命令不管哪种模式都要问，也不能「以后都允许」。只读模式下会拒绝执行。读可能存着密钥的文件也要问。') }}</p>
      <p class="safety">{{ t('电脑任务只有「只读」和「先问再改」，不提供工作区内自动。受保护的位置每次都要确认，也不能记住授权。') }}</p>
    </div>

    <div class="card computer-settings" :aria-busy="inventoryBusy">
      <h3>{{ t('电脑任务') }}</h3>
      <label class="inventory-toggle">
        <input v-model="inventory" type="checkbox" :disabled="!inventoryReady || inventoryBusy" @change="saveInventory" />
        <span>{{ t('告诉她这台电脑装了什么') }}</span>
      </label>
      <p class="rule-note">{{ t('默认打开。只提供系统、常用目录、程序和命令行工具的名字，不读取文件内容。关掉后只提供系统和常用目录。每个新电脑任务开始时固定这份说明。') }}</p>
      <button class="btn" :disabled="inventoryBusy || !inventoryReady" :aria-expanded="previewOpen" aria-controls="computer-context-preview" @click="togglePreview">{{ previewOpen ? t('收起环境说明') : t('看看具体写了什么') }}</button>
      <button v-if="computerError && !inventoryReady" class="btn" :disabled="inventoryBusy" @click="loadComputer">{{ t('重试') }}</button>
      <p v-if="computerError" class="msg error" role="alert">{{ computerError }}</p>
      <p v-if="computerNotice" class="msg ok" role="status">{{ computerNotice }}</p>
      <div v-if="previewOpen" id="computer-context-preview" class="context-preview" :aria-busy="previewLoading">
        <p v-if="previewError" class="msg error" role="alert">{{ previewError }}</p>
        <p v-if="previewLoading" role="status">{{ t('正在生成环境说明…') }}</p>
        <template v-else>
          <pre v-if="computerContext" tabindex="0" :aria-label="t('电脑任务的环境说明')">{{ computerContext }}</pre>
          <button class="btn" :disabled="inventoryBusy" @click="preview">{{ t('重新读取') }}</button>
        </template>
      </div>
    </div>

    <div class="card" :aria-busy="loading || Boolean(removing)">
      <div class="rule-heading">
        <h3>{{ t('记住的命令') }}</h3>
        <button class="btn" :disabled="loading || Boolean(removing)" @click="load">{{ loading ? t('正在读取…') : t('刷新') }}</button>
      </div>
      <p class="rule-note">{{ t('这里按项目或「电脑」范围列出按原文匹配的命令。删掉后立即生效，不会停止正在跑的命令。电脑任务的授权单独保存，不和项目混用。') }}</p>
      <p v-if="error" class="msg error" role="alert">{{ error }}</p>
      <p v-if="notice" class="msg ok" role="status">{{ notice }}</p>
      <p v-if="loading && !groups.length" class="empty" role="status">{{ t('正在读取记住的命令…') }}</p>
      <p v-else-if="!projects.length && !error" class="empty">{{ t('还没有记住的命令。确认命令时选「以后都允许」，才会出现在这里。') }}</p>
      <section v-for="group in projects" :key="group.project" class="project">
        <h4 class="project-path">{{ group.project === 'computer' ? t('电脑') : group.project }}</h4>
        <ul class="commands">
          <li v-for="command in group.commands" :key="command">
            <code>{{ command }}</code>
            <button class="btn danger" :disabled="loading || Boolean(removing)" :aria-label="t('删掉 {where} 记住的命令：{cmd}', { where: group.project === 'computer' ? t('电脑') : group.project, cmd: command })" @click="remove(group.project, command)">
              {{ removing === JSON.stringify([group.project, command]) ? t('正在删…') : t('删掉') }}
            </button>
          </li>
        </ul>
      </section>
    </div>

    <LocalAgentsCard v-if="bridge" />
    <SchedulesCard v-if="bridge" />

    <p class="hint moved">{{ t('外部工具（MCP）的加、删、开关移到了设置里的「技能和扩展」，和扩展包、技能包放在一起。') }}</p>
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
.inventory-toggle { display: flex; align-items: center; gap: 10px; cursor: pointer; }
.inventory-toggle input { width: 18px; height: 18px; accent-color: var(--accent); }
.context-preview { margin-top: 14px; }
.context-preview pre { max-height: 360px; overflow: auto; padding: 14px; border: 1px solid var(--line); border-radius: var(--ui-r); background: var(--ui-code); color: var(--label); white-space: pre-wrap; overflow-wrap: anywhere; font: 13px/1.7 var(--ui-mono); }
.inventory-toggle:focus-within, .context-preview pre:focus-visible { outline: 2px solid var(--focus); outline-offset: 3px; }
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
