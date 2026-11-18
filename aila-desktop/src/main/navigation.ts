/**
 * 窗口只显示艾拉自己的页面：不许跳到别的网址，不许开新窗口。
 *
 * 为什么：两个窗口都带着预加载的 window.aila（能开编程任务、加 MCP 服务……）。要是哪天她回答里的链接能点了、
 * 或者页面里混进了别的地址，跳过去的网页就也拿到了这些能力。所以除了自己的页面（开发时的 Vite 地址、
 * 打包后的 renderer/index.html），跳转一律拦下，window.open 一律不开（要打开网页走对话里有确认的 open_url）。
 */
import type { WebContents } from 'electron'
import { pathToFileURL } from 'node:url'

/** 是不是艾拉自己的页面：开发时同一个 Vite 地址（只看协议、主机、端口），打包后同一个 index.html（# 后面随便） */
export function isAppUrl(url: string, devUrl: string | undefined, rendererFile: string): boolean {
  let target: URL
  try {
    target = new URL(url)
  } catch {
    return false
  }
  if (devUrl) {
    try {
      return target.origin === new URL(devUrl).origin
    } catch {
      return false
    }
  }
  const page = pathToFileURL(rendererFile)
  return target.protocol === 'file:' && decodeURIComponent(target.pathname).toLowerCase() === decodeURIComponent(page.pathname).toLowerCase()
}

/** 给一个窗口的页面加上限制（在 app 的 web-contents-created 里对每个页面调用） */
export function lockDown(contents: WebContents, devUrl: string | undefined, rendererFile: string): void {
  contents.on('will-navigate', (event, url) => {
    if (!isAppUrl(url, devUrl, rendererFile)) event.preventDefault()
  })
  contents.on('will-redirect', (event, url) => {
    if (!isAppUrl(url, devUrl, rendererFile)) event.preventDefault()
  })
  contents.on('will-attach-webview', (event) => event.preventDefault())
  contents.setWindowOpenHandler(() => ({ action: 'deny' }))
}
