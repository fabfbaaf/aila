<script setup lang="ts">
import { computed, onMounted, ref, type Component } from 'vue'
import './settings.css'
import type { SettingsSection } from '../../../shared/settings'
import { bridge } from '../platform/bridge'
import { startStore } from './store'
import ModelSection from './sections/ModelSection.vue'
import VoiceSection from './sections/VoiceSection.vue'
import SkillsSection from './sections/SkillsSection.vue'
import KnowledgeSection from './sections/KnowledgeSection.vue'
import CodeSection from './sections/CodeSection.vue'
import MemorySection from './sections/MemorySection.vue'
import AccountSection from './sections/AccountSection.vue'
import AppearanceSection from './sections/AppearanceSection.vue'
import StatusSection from './sections/StatusSection.vue'

const SECTIONS: { id: SettingsSection; name: string; component: Component }[] = [
  { id: 'model', name: '对话', component: ModelSection },
  { id: 'voice', name: '语音', component: VoiceSection },
  { id: 'skills', name: '技能包', component: SkillsSection },
  { id: 'knowledge', name: '资料库', component: KnowledgeSection },
  { id: 'code', name: '代码库', component: CodeSection },
  { id: 'memory', name: '她记得的事', component: MemorySection },
  { id: 'account', name: '账号与同步', component: AccountSection },
  { id: 'appearance', name: '形象', component: AppearanceSection },
  { id: 'status', name: '运行状态', component: StatusSection }
]

function fromHash(): SettingsSection {
  const id = location.hash.replace('#/settings/', '') as SettingsSection
  return SECTIONS.some((s) => s.id === id) ? id : 'model'
}

const current = ref<SettingsSection>(fromHash())
const view = computed(() => SECTIONS.find((s) => s.id === current.value)!.component)

function go(id: SettingsSection): void {
  current.value = id
  history.replaceState(null, '', `#/settings/${id}`)
}

onMounted(() => {
  document.title = '艾拉 · 设置'
  void startStore()
  bridge?.onSettingsSection(go) // 右键菜单点了「资料库…」之类，切到对应分区
})
</script>

<template>
  <div v-if="!bridge" class="no-bridge">设置只在桌面版里能用（npm run dev）。</div>
  <div v-else class="layout">
    <nav class="side">
      <div class="brand">艾拉 · 设置</div>
      <button v-for="s in SECTIONS" :key="s.id" :class="{ active: s.id === current }" @click="go(s.id)">
        {{ s.name }}
      </button>
    </nav>
    <main class="content">
      <KeepAlive>
        <component :is="view" :key="current" />
      </KeepAlive>
    </main>
  </div>
</template>

<style scoped>
.layout {
  display: flex;
  height: 100%;
}

.side {
  flex: none;
  width: 168px;
  padding: 18px 10px;
  border-right: 1px solid var(--line);
  background: #fff;
}

.brand {
  margin: 0 8px 16px;
  font-size: 15px;
  font-weight: 600;
}

.side button {
  display: block;
  width: 100%;
  margin-bottom: 2px;
  padding: 8px 10px;
  border: none;
  border-radius: 8px;
  background: none;
  color: #4a5068;
  font: inherit;
  text-align: left;
  cursor: pointer;
}

.side button:hover {
  background: #f3f4f8;
}

.side button.active {
  background: var(--accent-soft);
  color: var(--accent);
  font-weight: 600;
}

.content {
  flex: 1;
  overflow-y: auto;
  padding: 24px 28px 40px;
}

.no-bridge {
  padding: 40px;
  color: var(--muted);
}
</style>
