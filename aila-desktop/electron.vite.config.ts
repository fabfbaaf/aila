import { resolve } from 'path'
import { defineConfig, externalizeDepsPlugin } from 'electron-vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  main: {
    plugins: [externalizeDepsPlugin()]
  },
  preload: {
    plugins: [externalizeDepsPlugin()]
  },
  renderer: {
    resolve: {
      alias: { '@': resolve('src/renderer/src') }
    },
    plugins: [vue()],
    // 只听 IPv4：新版 Node 把 localhost 解析成 ::1，Electron 去连 127.0.0.1 会连不上
    server: { host: '127.0.0.1' }
  }
})
