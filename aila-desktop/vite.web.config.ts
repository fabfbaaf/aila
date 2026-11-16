// 只跑渲染层，在普通浏览器里调试角色和行为（没有 Electron 的窗口能力）
import { resolve } from 'path'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  root: resolve(__dirname, 'src/renderer'),
  resolve: {
    alias: { '@': resolve(__dirname, 'src/renderer/src') }
  },
  plugins: [vue()],
  server: {
    port: 5180,
    strictPort: true,
    // 只听 IPv4：新版 Node 把 localhost 解析成 ::1，有的浏览器和工具连不上只听 IPv6 的服务
    host: '127.0.0.1',
    // 对话请求转给本地 AI 服务（桌面版由主进程转发，不走这里）
    proxy: {
      '/ai': {
        target: 'http://127.0.0.1:8765',
        ws: true, // 语音的 WebSocket 也转过去
        rewrite: (p) => p.replace(/^\/ai/, ''),
        // 连不上 AI 服务时回 502（Vite 默认回 500），好和「服务内部出错」区分开
        configure: (proxy) =>
          proxy.on('error', (_err, _req, res) => {
            if ('writeHead' in res && !res.headersSent) res.writeHead(502).end()
          })
      }
    }
  }
})
