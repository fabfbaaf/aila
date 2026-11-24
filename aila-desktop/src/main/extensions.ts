/**
 * 扩展包在主进程这边要做的事（第二、三期）：
 * - 扩展自带的页面：和她的作品一样，开在独立的沙盒窗口里，只放行这一个页面的地址（不能联网、碰不到本机服务的别的接口）
 * - 换成扩展带的形象：文件路径从 AI 服务的扩展列表里取（渲染层只说「哪个扩展的哪个形象」），还要确认确实在扩展文件夹的 avatars 里
 */
import { statSync } from 'node:fs'
import { isAbsolute } from 'node:path'
import { BrowserWindow, session } from 'electron'
import type { AiResult } from '../shared/settings'
import { tm } from './lang'

const PAGE_PARTITION = 'aila-extension-page'

export async function openExtensionPage(aiUrl: string, extId: string, name: string, title: string, parent: BrowserWindow | null): Promise<AiResult<{ ok: boolean }>> {
  const url = `${aiUrl}/extensions/${encodeURIComponent(extId)}/pages/${encodeURIComponent(name)}`
  const ses = session.fromPartition(PAGE_PARTITION)
  // 只放行这一个页面；页面里想联网、想访问本机别的接口一律拦下（页面本身也带了最严的 CSP）
  ses.webRequest.onBeforeRequest((details, cb) => cb({ cancel: details.url !== url }))
  ses.setPermissionRequestHandler((_wc, _perm, cb) => cb(false))
  const win = new BrowserWindow({
    width: 900,
    height: 680,
    title: tm('扩展 · {title}', { title }),
    parent: parent ?? undefined,
    autoHideMenuBar: true,
    backgroundColor: '#101218',
    webPreferences: { partition: PAGE_PARTITION, sandbox: true, contextIsolation: true, nodeIntegration: false }
  })
  win.setMenu(null)
  // 页面里的链接、window.open 都不跟（不打开新窗口，也不跳到别处）
  win.webContents.setWindowOpenHandler(() => ({ action: 'deny' }))
  win.webContents.on('will-navigate', (e, target) => {
    if (target !== url) e.preventDefault()
  })
  try {
    await win.loadURL(url)
  } catch (err) {
    win.close()
    return { ok: false, error: tm('打不开这个页面：{why}', { why: err instanceof Error ? err.message : String(err) }) }
  }
  return { ok: true, data: { ok: true } }
}

/** 拖进来的东西：绝对路径、存在、是文件夹或者 .zip（别的文件、空路径都不收） */
export function droppedPathOk(path: unknown, stat: (p: string) => { isDirectory(): boolean; isFile(): boolean } | null = safeStat): path is string {
  if (typeof path !== 'string' || !path || path.length > 1000 || !isAbsolute(path)) return false
  const st = stat(path)
  if (!st) return false
  return st.isDirectory() || (st.isFile() && path.toLowerCase().endsWith('.zip'))
}

function safeStat(path: string): { isDirectory(): boolean; isFile(): boolean } | null {
  try {
    return statSync(path)
  } catch {
    return null
  }
}
