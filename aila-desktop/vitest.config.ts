/** 组件测试沿用项目的 Vue 编译插件，不依赖浏览器或真实 Electron 窗口。 */
import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'

const compiler = vue()
const transform = compiler.transform
if (typeof transform !== 'function') throw new Error('Vue 测试编译器没有提供转换函数。')

export default defineConfig({
  plugins: [{
    ...compiler,
    // 内存渲染器要测客户端交互，不使用 Node 测试环境默认生成的服务端模板。
    transform(code, id, options) { return transform.call(this, code, id, { ...options, ssr: false }) }
  }]
})
