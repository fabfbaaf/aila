<script setup lang="ts">
/**
 * 主窗口「艾拉」：左边一列图标切页面，右边是页面本身。
 *
 * 以前的工作窗口、代码星图、设置三个窗口都搬进来当页面（桌面上的她还是自己的透明窗口）。
 * 页面用 KeepAlive 留着：切回任务页时她做到哪一步还在原处；地图例外，只在看的时候才挂着（3D 一直画很费电）。
 * 页面和地址的对应规则在 shared/shell.ts，主进程也用同一份。
 * 界面风格（颜色、字体、图标）在 theme/：tokens.css、icons.ts。
 */
import { t } from '../i18n'
import { computed, defineAsyncComponent, nextTick, onBeforeUnmount, onMounted, ref, type Component } from 'vue'
import { PAGES, parseHash, toHash, type ShellRoute } from '../../../shared/shell'
import { bridge } from '../platform/bridge'
import { startStore } from '../settings/store'
import '../theme/tokens.css'
import '../settings/settings.css'
import Icon from '../theme/Icon.vue'
import BrandMark from '../theme/BrandMark.vue'
import AgentApp from '../agent/AgentApp.vue'
import ProjectsPage from './ProjectsPage.vue'
import SectionPage from './SectionPage.vue'
import SettingsPage from './SettingsPage.vue'
import ChatHistoryPage from './ChatHistoryPage.vue'
import StudioPage from './StudioPage.vue'
import CommandPalette from './CommandPalette.vue'
import IntroLayer from './IntroLayer.vue'
import { chatQueryRequest, newTaskRequest } from './intents'
import type { PaletteAct } from './palette'

const KnowledgeSection = defineAsyncComponent(() => import('../settings/sections/KnowledgeSection.vue'))
const MemorySection = defineAsyncComponent(() => import('../settings/sections/MemorySection.vue'))

const route = ref<ShellRoute>(parseHash(location.hash))
/** 「等你」收件箱：有几个任务在等你（任务页报上来） */
const waiting = ref(0)
const offs: (() => void)[] = []

const page = computed<{ component: Component; props: Record<string, unknown> }>(() => {
  switch (route.value.page) {
    case 'chat':
      return { component: ChatHistoryPage, props: {} }
    case 'projects':
      return { component: ProjectsPage, props: { tab: route.value.tab ?? 'list' } }
    case 'knowledge':
      return { component: SectionPage, props: { section: KnowledgeSection, title: t('资料'), hint: t('她回答问题、写代码前会先翻这里的文档') } }
    case 'memory':
      return { component: SectionPage, props: { section: MemorySection, title: t('记忆'), hint: t('她记得的事和经历：聊天、开任务时挑相关的几条用上') } }
    case 'studio':
      return { component: StudioPage, props: { work: route.value.work } }
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
  document.title = name && next.page !== 'tasks' ? t('艾拉 · {page}', { page: t(name) }) : t('艾拉')
}

// ---------- Ctrl+K 快捷跳转（palette.ts） ----------

const paletteOpen = ref(false)
const palette = ref<InstanceType<typeof CommandPalette> | null>(null)

async function openPalette(): Promise<void> {
  paletteOpen.value = true
  await nextTick()
  await palette.value?.open()
}

function act(a: PaletteAct): void {
  if (a.kind === 'go') go(a.route)
  else if (a.kind === 'open-chat') bridge?.openChat()
  else if (a.kind === 'new-task') {
    go({ page: 'tasks' })
    newTaskRequest.value++
  } else if (a.kind === 'chat') {
    go({ page: 'chat' })
    chatQueryRequest.value = a.query
  }
}

/** Ctrl+1…7 切页面（和图标栏的顺序一样）；Ctrl+K 快捷跳转 */
function onKey(e: KeyboardEvent): void {
  if (!e.ctrlKey || e.altKey || e.shiftKey || e.metaKey) return
  if (e.key.toLowerCase() === 'k') {
    e.preventDefault()
    if (paletteOpen.value) paletteOpen.value = false
    else void openPalette()
    return
  }
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
    <nav class="rail" :aria-label="t('页面')">
      <div class="brand" :title="t('艾拉')"><BrandMark :size="44" /></div>
      <button
        v-for="p in PAGES"
        :key="p.id"
        class="ic"
        :class="{ on: route.page === p.id, end: p.id === 'settings' }"
        :title="t('{page}（Ctrl+{key}；Ctrl+K 快捷跳转）', { page: t(p.name), key: p.key })"
        :aria-current="route.page === p.id ? 'page' : undefined"
        @click="go(p.id === 'projects' ? { page: 'projects', tab: route.tab ?? 'list' } : { page: p.id })"
      >
        <Icon :name="p.id" :size="20" />
        <b v-if="p.id === 'tasks' && waiting" class="badge" :title="t('{n} 个任务在等你', { n: waiting })">{{ waiting }}</b>
        <span>{{ t(p.name) }}</span>
      </button>
    </nav>
    <main class="page">
      <KeepAlive>
        <component
          :is="page.component"
          :key="route.page"
          v-bind="page.props"
          @go="go"
          @waiting="(n: number) => (waiting = n)"
        />
      </KeepAlive>
    </main>
    <CommandPalette v-if="paletteOpen" ref="palette" @close="paletteOpen = false" @act="act" />
    <!-- 开场演出（shared/intro.ts）：第一次打开、或者设置里点「再看一遍」时盖在最上面 -->
    <IntroLayer @go="go" />
  </div>
</template>

<style scoped>
.shell {
  display: grid;
  grid-template-columns: 64px minmax(0, 1fr);
  height: 100%;
}

.rail {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  padding: 12px 0 10px;
  background: var(--ui-sunken);
  border-right: 1px solid var(--ui-line);
}

/* 标志：「艾拉」字标（theme/BrandMark.vue），和 exe、托盘图标一样 */
.brand {
  display: grid;
  place-items: center;
  margin-bottom: 14px;
}

.ic {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 3px;
  width: 52px;
  padding: 8px 0 6px;
  border: none;
  border-radius: var(--ui-r);
  background: none;
  color: var(--ui-muted);
  font: inherit;
  font-size: 11px;
  cursor: pointer;
  transition: background var(--ui-fast), color var(--ui-fast);
}

.ic:hover {
  background: var(--ui-surface);
  color: var(--ui-text);
}

.ic.on {
  background: var(--ui-accent-soft);
  color: var(--ui-accent-hi);
}

/* 当前页左边一道竖条 */
.ic.on::before {
  content: '';
  position: absolute;
  left: -6px;
  top: 10px;
  bottom: 10px;
  width: 3px;
  border-radius: 0 3px 3px 0;
  background: var(--ui-accent);
}

.ic:focus-visible {
  outline: none;
  box-shadow: var(--ui-ring);
}

.badge {
  position: absolute;
  top: 3px;
  right: 7px;
  min-width: 16px;
  height: 16px;
  padding: 0 4px;
  box-sizing: border-box;
  border-radius: 999px;
  background: var(--ui-warn);
  color: #1d1300;
  font-size: 10.5px;
  font-weight: 700;
  line-height: 16px;
  text-align: center;
}

.ic.end {
  margin-top: auto;
}

.page {
  min-width: 0;
  height: 100%;
  overflow: hidden;
  background: var(--ui-bg);
}
</style>
