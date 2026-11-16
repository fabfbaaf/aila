<script setup lang="ts">
/**
 * 项目页：登记过的项目（索引状态，原来在「设置 → 代码库」）和地图（原来的代码星图窗口）。
 * 地图只在这一页、这一块正显示着的时候才挂上：离开时卸掉，3D 不在后台一直画，摄像头（手势）也随之关掉。
 */
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
      <b>项目</b>
      <div class="tabs" role="tablist">
        <button role="tab" :aria-selected="tab === 'list'" :class="{ on: tab === 'list' }" @click="emit('go', { page: 'projects', tab: 'list' })">
          列表
        </button>
        <button role="tab" :aria-selected="tab === 'map'" :class="{ on: tab === 'map' }" @click="emit('go', { page: 'projects', tab: 'map' })">
          地图
        </button>
      </div>
      <span class="hint">{{ tab === 'map' ? '她最近读过、改过的文件会亮起来' : '登记过的项目：她按意思搜代码、按报错定位都靠这里的索引' }}</span>
    </header>
    <div v-show="tab === 'list'" class="settings-scope list">
      <div v-if="!bridge" class="empty">项目列表只在桌面版里能用（npm run dev）。</div>
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
  padding: 9px 18px;
  border-bottom: 1px solid #262b37;
  background: #12151c;
}

.tabs {
  display: flex;
  gap: 4px;
}

.tabs button {
  padding: 3px 12px;
  border: 1px solid #262b37;
  border-radius: 7px;
  background: none;
  color: #8a92a3;
  font: inherit;
  font-size: 13px;
  cursor: pointer;
}

.tabs button.on {
  border-color: #a78bfa;
  background: rgba(167, 139, 250, 0.16);
  color: #d7dce5;
}

.hint {
  color: #8a92a3;
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
