import { createApp, type Component } from 'vue'

// 同一个页面四种用途：桌面上的她（默认）、设置窗口（#/settings/分区）、代码星图（#/workbench）、工作窗口（#/agent）。
// 按需加载：各自的全局样式不一样（角色窗口全透明，设置窗口浅色，星图深色），星图的 3D 代码也只在星图窗口里加载
const hash = location.hash
const load: Promise<{ default: Component }> = hash.startsWith('#/settings')
  ? import('./settings/SettingsApp.vue')
  : hash.startsWith('#/workbench')
    ? import('./workbench/WorkbenchApp.vue')
    : hash.startsWith('#/agent')
      ? import('./agent/AgentApp.vue')
      : import('./App.vue')

void load.then((m) => createApp(m.default).mount('#app'))
