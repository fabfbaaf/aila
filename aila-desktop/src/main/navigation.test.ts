import { describe, expect, it, vi } from 'vitest'
import { isAppUrl, lockDown } from './navigation'

const FILE = 'C:\\app\\resources\\app.asar\\out\\renderer\\index.html'

describe('窗口只显示自己的页面', () => {
  it('开发时只认 Vite 的地址', () => {
    const dev = 'http://127.0.0.1:5173'
    expect(isAppUrl('http://127.0.0.1:5173/#/tasks', dev, FILE)).toBe(true)
    expect(isAppUrl('http://127.0.0.1:5173/', dev, FILE)).toBe(true)
    for (const bad of ['http://127.0.0.1:8765/settings', 'https://evil.example/', 'http://localhost:5173/', 'javascript:alert(1)', 'not a url']) {
      expect(isAppUrl(bad, dev, FILE)).toBe(false)
    }
  })

  it('打包后只认自己的 index.html', () => {
    expect(isAppUrl('file:///C:/app/resources/app.asar/out/renderer/index.html#/settings', undefined, FILE)).toBe(true)
    expect(isAppUrl('file:///C:/Users/me/Downloads/evil.html', undefined, FILE)).toBe(false)
    expect(isAppUrl('https://evil.example/', undefined, FILE)).toBe(false)
  })

  it('跳转到别处被拦下，新窗口一律不开', () => {
    const handlers = new Map<string, (event: { preventDefault: () => void }, url?: string) => void>()
    let openHandler: (() => { action: string }) | undefined
    const contents = {
      on: (name: string, fn: (event: { preventDefault: () => void }, url?: string) => void) => handlers.set(name, fn),
      setWindowOpenHandler: (fn: () => { action: string }) => { openHandler = fn }
    }
    lockDown(contents as never, 'http://127.0.0.1:5173', FILE)
    const stop = vi.fn()
    handlers.get('will-navigate')!({ preventDefault: stop }, 'https://evil.example/')
    expect(stop).toHaveBeenCalledTimes(1)
    handlers.get('will-navigate')!({ preventDefault: stop }, 'http://127.0.0.1:5173/#/tasks')
    expect(stop).toHaveBeenCalledTimes(1)
    handlers.get('will-redirect')!({ preventDefault: stop }, 'https://evil.example/')
    handlers.get('will-attach-webview')!({ preventDefault: stop })
    expect(stop).toHaveBeenCalledTimes(3)
    expect(openHandler?.()).toEqual({ action: 'deny' })
  })
})
