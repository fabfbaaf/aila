<script setup lang="ts">
/**
 * 主窗口「艾拉」：左边一列图标切页面，右边是页面本身。
 *
 * 以前的工作窗口、代码星图、设置三个窗口都搬进来当页面（桌面上的她还是自己的透明窗口）。
 * 页面用 KeepAlive 留着：切回任务页时她做到哪一步还在原处；地图例外，只在看的时候才挂着（3D 一直画很费电）。
 * 页面和地址的对应规则在 shared/shell.ts，主进程也用同一份。
 */
import { computed, defineAsyncComponent, onBeforeUnmount, onMounted, ref, type Component } from 'vue'
import { PAGES, parseHash, toHash, type ShellRoute } from '../../../shared/shell'
import { bridge } from '../platform/bridge'
import { startStore } from '../settings/store'
import '../settings/settings.css'
import AgentApp from '../agent/AgentApp.vue'
import ProjectsPage from './ProjectsPage.vue'
import SectionPage from './SectionPage.vue'
import SettingsPage from './SettingsPage.vue'

const KnowledgeSection = defineAsyncComponent(() => import('../settings/sections/KnowledgeSection.vue'))
const MemorySection = defineAsyncComponent(() => import('../settings/sections/MemorySection.vue'))

const route = ref<ShellRoute>(parseHash(location.hash))
const offs: (() => void)[] = []

const page = computed<{ component: Component; props: Record<string, unknown> }>(() => {
  switch (route.value.page) {
    case 'projects':
      return { component: ProjectsPage, props: { tab: route.value.tab ?? 'list' } }
    case 'knowledge':
      return { component: SectionPage, props: { section: KnowledgeSection } }
    case 'memory':
      return { component: SectionPage, props: { section: MemorySection } }
    case 'settings':
      return { component: SettingsPage, props: { section: route.value.section ?? 'model' } }
    default:
      return { component: AgentApp, props: {} }
  }
})

function go(next: ShellRoute): void {
  route.value = next
  history.replaceState(null, '', `#${toHash(next)}`)
  const name = PAGES.find((p) => p.id === next.page)?.name
  document.title = name && next.page !== 'tasks' ? `艾拉 · ${name}` : '艾拉'
}

/** Ctrl+1…5 切页面（和图标栏的顺序一样） */
function onKey(e: KeyboardEvent): void {
  if (!e.ctrlKey || e.altKey || e.shiftKey || e.metaKey) return
  const target = PAGES.find((p) => p.key === e.key)
  if (!target) return
  e.preventDefault()
  go(target.id === 'projects' ? { page: 'projects', tab: 'list' } : { page: target.id })
}

onMounted(() => {
  go(route.value)
  window.addEventListener('keydown', onKey)
  void startStore()
  // 右键菜单、星图、屏幕上的报错这些入口要打开某一页：主进程检查过地址再发过来
  if (bridge) offs.push(bridge.onShellGo(go))
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKey)
  for (const off of offs.splice(0)) off()
})
</script>

<template>
  <div class="shell">
    <nav class="rail" aria-label="页面">
      <div class="brand" title="艾拉">艾</div>
      <button
        v-for="p in PAGES"
        :key="p.id"
        class="ic"
        :class="{ on: route.page === p.id, end: p.id === 'settings' }"
        :title="`${p.name}（Ctrl+${p.key}）`"
        :aria-current="route.page === p.id ? 'page' : undefined"
        @click="go(p.id === 'projects' ? { page: 'projects', tab: route.tab ?? 'list' } : { page: p.id })"
      >
        <i aria-hidden="true">{{ p.icon }}</i>
        <span>{{ p.name }}</span>
      </button>
    </nav>
    <main class="page">
      <KeepAlive>
        <component
          :is="page.component"
          :key="route.page"
          v-bind="page.props"
          @go="go"
        />
      </KeepAlive>
    </main>
  </div>
</template>

<style>
html,
body,
#app {
  margin: 0;
  height: 100%;
  overflow: hidden;
  background: #0f1117;
  color: #d7dce5;
  font-family: 'Microsoft YaHei', 'PingFang SC', system-ui, sans-serif;
  font-size: 14px;
}
</style>

<style scoped>
.shell {
  display: grid;
  grid-template-columns: 58px minmax(0, 1fr);
  height: 100%;
}

.rail {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  padding: 10px 0;
  background: #0b0d12;
  border-right: 1px solid #262b37;
}

.brand {
  width: 32px;
  height: 32px;
  margin-bottom: 10px;
  border-radius: 9px;
  background: rgba(167, 139, 250, 0.16);
  color: #a78bfa;
  font-weight: 700;
  line-height: 32px;
  text-align: center;
}

.ic {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 1px;
  width: 46px;
  padding: 6px 0 5px;
  border: none;
  border-radius: 9px;
  background: none;
  color: #8a92a3;
  font: inherit;
  font-size: 11px;
  cursor: pointer;
}

.ic i {
  font-style: normal;
  font-size: 16px;
  line-height: 1.2;
}

.ic:hover {
  background: #161a23;
  color: #d7dce5;
}

.ic.on {
  background: rgba(167, 139, 250, 0.16);
  color: #a78bfa;
}

.ic:focus-visible {
  outline: 2px solid #a78bfa;
  outline-offset: 1px;
}

.ic.end {
  margin-top: auto;
}

.page {
  min-width: 0;
  height: 100%;
  overflow: hidden;
}
</style>
