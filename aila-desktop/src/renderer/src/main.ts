import { createApp, type Component } from 'vue'

// 同一个页面两种用途：桌面上的她（默认），或者设置窗口（地址带 #/settings/分区）。
// 按需加载：两边的全局样式不一样（角色窗口全透明，设置窗口有背景）
const load: Promise<{ default: Component }> = location.hash.startsWith('#/settings')
  ? import('./settings/SettingsApp.vue')
  : import('./App.vue')

void load.then((m) => createApp(m.default).mount('#app'))
