<script setup lang="ts">
/** 设置页：剩下的几个分区（资料库、记忆、代码库各自成了一页），左边一列切换 */
import { computed, type Component } from 'vue'
import type { SettingsSection } from '../../../shared/settings'
import { SETTINGS_TABS, type ShellRoute } from '../../../shared/shell'
import { bridge } from '../platform/bridge'
import ModelSection from '../settings/sections/ModelSection.vue'
import VoiceSection from '../settings/sections/VoiceSection.vue'
import SkillsSection from '../settings/sections/SkillsSection.vue'
import AccountSection from '../settings/sections/AccountSection.vue'
import AppearanceSection from '../settings/sections/AppearanceSection.vue'
import StatusSection from '../settings/sections/StatusSection.vue'

const props = defineProps<{ section: SettingsSection }>()
const emit = defineEmits<{ go: [route: ShellRoute] }>()

const VIEWS: Partial<Record<SettingsSection, Component>> = {
  model: ModelSection,
  voice: VoiceSection,
  skills: SkillsSection,
  account: AccountSection,
  appearance: AppearanceSection,
  status: StatusSection
}
const view = computed(() => VIEWS[props.section] ?? ModelSection)
</script>

<template>
  <div class="settings-scope settings-page">
    <nav class="side">
      <div class="title">设置</div>
      <button
        v-for="s in SETTINGS_TABS"
        :key="s.id"
        :class="{ active: s.id === section }"
        @click="emit('go', { page: 'settings', section: s.id })"
      >
        {{ s.name }}
      </button>
    </nav>
    <div class="content">
      <div v-if="!bridge" class="empty">设置只在桌面版里能用（npm run dev）。</div>
      <KeepAlive v-else>
        <component :is="view" :key="section" />
      </KeepAlive>
    </div>
  </div>
</template>

<style scoped>
.settings-page {
  display: flex;
  height: 100%;
  background: var(--bg);
}

.side {
  flex: none;
  width: 168px;
  padding: 18px 10px;
  border-right: 1px solid var(--line);
  background: #12151c;
}

.title {
  margin: 0 8px 14px;
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
  color: var(--label);
  font: inherit;
  text-align: left;
  cursor: pointer;
}

.side button:hover {
  background: var(--hover);
}

.side button.active {
  background: var(--accent-soft);
  color: var(--accent);
  font-weight: 600;
}

.content {
  flex: 1;
  min-width: 0;
  overflow-y: auto;
  padding: 24px 28px 40px;
}

.content > * {
  max-width: 860px;
}
</style>
