import { createApp, type Component } from 'vue'
import { initLang } from './i18n'

// 同一个页面两种用途：桌面上的她（没有地址）和主窗口「艾拉」（#/页面，见 shared/shell.ts）。
// 主窗口里是任务、项目（含地图）、资料、记忆、设置几页；旧地址 #/agent、#/workbench、#/settings/分区 也进主窗口。
// 按需加载：角色窗口全透明，主窗口深色，各自的全局样式和代码只在自己的窗口里加载
const load = (): Promise<{ default: Component }> => (location.hash.startsWith('#/') ? import('./shell/ShellApp.vue') : import('./App.vue'))

// 启动卡片（#launch）：透明的小窗口，只有字标，没有预加载接口，也不用定语言
if (location.hash === '#launch') void import('./shell/LaunchCard.vue').then((m) => createApp(m.default).mount('#app'))
// 先定下语言再画，不然会先闪一下中文
else void Promise.all([load(), initLang()]).then(([m]) => createApp(m.default).mount('#app'))
