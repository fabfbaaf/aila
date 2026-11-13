<script setup lang="ts">
import { onActivated, ref } from 'vue'
import type { SkillInfo } from '../../../../shared/cloud'
import type { AiSettings } from '../../../../shared/settings'
import { bridge } from '../../platform/bridge'
import Toggle from '../Toggle.vue'

type SkillRow = SkillInfo

const api = bridge!
const skills = ref<SkillRow[] | null>(null)
const mcp = ref<AiSettings['mcp']>([])
const mcpPath = ref('')
const loaded = ref(false)

async function load(): Promise<void> {
  const [list, settings] = await Promise.all([api.skillsList(), api.aiSettings()])
  skills.value = list
  mcp.value = settings.data?.mcp ?? []
  mcpPath.value = settings.data?.paths.mcp ?? ''
  loaded.value = true
}

function toggle(s: SkillRow, on: boolean): void {
  api.skillsSet(s.id, on)
  s.enabled = on
}

function unavailable(s: SkillRow): string {
  return s.id === 'knowledge' ? '资料库里还没有文件' : '暂时用不了'
}

onActivated(load)
</script>

<template>
  <h2>技能包</h2>
  <p class="lead">她能用的工具按技能包分组，可以单独开关。关掉后她就不会再用那一类工具。登录后开关跟着账号同步。</p>

  <div v-if="loaded && !skills" class="offline">
    连不上 AI 服务，请先启动 aila-ai。
    <button class="link" @click="load">重试</button>
  </div>

  <div v-for="s in skills ?? []" :key="s.id" class="card skill">
    <div class="top">
      <div>
        <h3>{{ s.name }}</h3>
        <span class="hint">{{ s.description }}{{ s.available ? '' : `（${unavailable(s)}）` }}</span>
      </div>
      <Toggle :model-value="s.enabled" @update:model-value="(v) => toggle(s, v)" />
    </div>
    <div v-if="s.tools?.length" class="tools">
      <span v-for="t in s.tools" :key="t.name" class="tag" :class="{ warn: t.risk !== 'read' }">
        {{ t.name }}{{ t.risk === 'mixed' ? ' · 有时要你确认' : t.risk !== 'read' ? ' · 要你确认' : '' }}
      </span>
    </div>
  </div>

  <div class="card">
    <h3>外部工具（MCP）</h3>
    <p class="hint" style="margin-top: 0">
      支持 MCP 协议的工具服务都能接进来，每个服务是一个技能包（默认关闭）。在
      <code>{{ mcpPath || 'aila-ai/mcp.json' }}</code>
      里配置（格式见 mcp.example.json），改完重启 aila-ai。只读的工具可以直接用；会改东西的工具每次用之前她都会先问你，气泡里列出具体内容，你点「好」才执行。
    </p>
    <table v-if="mcp.length" class="list">
      <tr v-for="m in mcp" :key="m.id">
        <td>{{ m.name }}</td>
        <td>
          <span v-if="m.connected" class="tag good">已连接</span>
          <span v-else class="tag warn" :title="m.error ?? ''">没连上</span>
        </td>
        <td class="hint">{{ m.error }}</td>
      </tr>
    </table>
    <p v-else class="empty">还没有配置外部工具。</p>
  </div>
</template>

<style scoped>
.skill .top {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
}

.skill h3 {
  margin-bottom: 2px;
}

.tools {
  margin-top: 8px;
}

code {
  padding: 0 4px;
  border-radius: 4px;
  background: #f1f2f7;
  font-size: 11.5px;
}
</style>
