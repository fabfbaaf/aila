/// <reference types="vite/client" />
import type { AilaApi } from '../../preload/index'

declare global {
  interface Window {
    /** 只在 Electron 里存在；普通浏览器里是 undefined */
    aila?: AilaApi
  }
}

declare module '*.vue' {
  import type { DefineComponent } from 'vue'
  const component: DefineComponent<object, object, unknown>
  export default component
}

export {}
