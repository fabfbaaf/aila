<script setup lang="ts">
/**
 * 项目页：登记过的项目（索引状态，原来在「设置 → 代码库」）和地图（原来的代码星图窗口）。
 * 地图只在这一页、这一块正显示着的时候才挂上：离开时卸掉，3D 不在后台一直画，摄像头（手势）也随之关掉。
 */
import { t } from '../i18n'
import { defineAsyncComponent, onActivated, onDeactivated, ref } from 'vue'
import type { ProjectsTab, ShellRoute } from '../../../shared/shell'
import { bridge } from '../platform/bridge'
import CodeSection from '../settings/sections/CodeSection.vue'

const WorkbenchApp = defineAsyncComponent(() => import('../workbench/WorkbenchApp.vue'))

defineProps<{ tab: ProjectsTab }>()
const emit = defineEmits<{ go: [route: ShellRoute] }>()

/** 这一页正显示着（KeepAlive 留着但切走了的时候是 false） */
const shown = ref(true)
onActivated(() => (shown.value = true))
onDeactivated(() => (shown.value = false))
</script>

<template>
  <div class="projects">
    <header class="bar">
      <b>{{ t('项目') }}</b>
      <div class="tabs" role="tablist">
        <button role="tab" :aria-selected="tab === 'list'" :class="{ on: tab === 'list' }" @click="emit('go', { page: 'projects', tab: 'list' })">
          {{ t('列表') }}
        </button>
        <button role="tab" :aria-selected="tab === 'map'" :class="{ on: tab === 'map' }" @click="emit('go', { page: 'projects', tab: 'map' })">
          {{ t('地图') }}
        </button>
      </div>
      <span class="hint">{{ tab === 'map' ? t('她最近读过、改过的文件会亮起来') : t('登记过的项目：她按意思搜代码、按报错定位都靠这里的索引') }}</span>
    </header>
    <div v-show="tab === 'list'" class="settings-scope list">
      <div v-if="!bridge" class="empty">{{ t('项目列表只在桌面版里能用（npm run dev）。') }}</div>
      <div v-else class="inner"><CodeSection /></div>
    </div>
    <div v-if="tab === 'map' && shown" class="map">
      <WorkbenchApp @close="emit('go', { page: 'projects', tab: 'list' })" />
    </div>
  </div>
</template>

<style scoped>
.projects {
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  height: 100%;
}

.bar {
  display: flex;
  align-items: center;
  gap: 14px;
  min-height: 52px;
  box-sizing: border-box;
  padding: 0 18px;
  border-bottom: 1px solid var(--ui-line);
  background: var(--ui-bg);
}

.bar b {
  font-size: 15px;
}

/* 和任务页的「过程 / 轨迹」同一种分段切换 */
.tabs {
  display: flex;
  gap: 2px;
  padding: 2px;
  border: 1px solid var(--ui-line);
  border-radius: var(--ui-r);
  background: var(--ui-sunken);
}

.tabs button {
  padding: 3px 12px;
  border: none;
  border-radius: 7px;
  background: none;
  color: var(--ui-muted);
  font: inherit;
  font-size: 12.5px;
  cursor: pointer;
  transition: background var(--ui-fast), color var(--ui-fast);
}

.tabs button:hover {
  color: var(--ui-text);
}

.tabs button.on {
  background: var(--ui-raised);
  color: var(--ui-text);
  box-shadow: var(--ui-shadow-s);
}

.tabs button:focus-visible {
  outline: none;
  box-shadow: var(--ui-ring);
}

.hint {
  color: var(--ui-muted);
  font-size: 12.5px;
}

.list {
  overflow-y: auto;
  background: var(--bg);
}

.inner {
  max-width: 860px;
  padding: 24px 32px 40px;
}

.map {
  min-height: 0;
  height: 100%;
}
</style>
