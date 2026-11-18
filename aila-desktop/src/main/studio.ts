/**
 * 主进程里和「她的作品」「日记」有关的事：调 AI 服务的接口，以及打开作品的预览窗口。
 *
 * 预览窗口：作品是她写的代码，只当作品看——单独的窗口、沙盒、没有预加载（碰不到本机的任何接口），
 * 单独的会话分区里只放行这件作品自己的文件（AI 服务给的时候还带了最严的 CSP：不能联网），
 * 页面里的链接、弹窗一律不开（全局的 lockDown 管）。
 */

import { BrowserWindow, session, shell } from 'electron'
import type { AiResult } from '../shared/settings'
import type { DiaryEntry, Work, WorkDetail } from '../shared/studio'
import { attempt, json } from './ai'

const enc = encodeURIComponent

function call<T>(aiUrl: string, method: string, path: string, body?: unknown, timeout = 15_000): Promise<AiResult<T>> {
  return attempt(async () =>
    json<T>(
      await fetch(`${aiUrl}${path}`, {
        method,
        ...(body === undefined ? {} : { headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) }),
        signal: AbortSignal.timeout(timeout)
      })
    )
  )
}

export interface StartWorkBody {
  hints?: { memories: string[]; tasks: string[]; occasion: string }
  idea?: { title: string; idea: string; kind: 'web' | 'script'; why: string }
  budget?: number
}

export const studio = {
  works: (aiUrl: string) => call<Work[]>(aiUrl, 'GET', '/studio/works'),
  work: (aiUrl: string, id: string) => call<WorkDetail>(aiUrl, 'GET', `/studio/works/${enc(id)}`),
  // 挑点子要问一次大模型：多给点时间
  start: (aiUrl: string, body: StartWorkBody) => call<Work>(aiUrl, 'POST', '/studio/works', body, 60_000),
  seen: (aiUrl: string, id: string) => call<{ ok: boolean }>(aiUrl, 'POST', `/studio/works/${enc(id)}/seen`),
  remove: (aiUrl: string, id: string) => call<{ ok: boolean }>(aiUrl, 'DELETE', `/studio/works/${enc(id)}`, undefined, 30_000),
  diary: (aiUrl: string) => call<DiaryEntry[]>(aiUrl, 'GET', '/studio/diary'),
  writeDiary: (aiUrl: string, day: string, facts: string[]) =>
    call<DiaryEntry>(aiUrl, 'POST', '/studio/diary', { day, facts }, 60_000)
}

/** 作品 id 的样子（和 AI 服务、shared/shell.ts 一样） */
export const WORK_ID = /^[\w-]{4,64}$/

const PREVIEW_PARTITION = 'aila-studio-preview'

/** 打开网页作品：单独的沙盒窗口，只能加载这件作品自己的文件 */
export async function openWorkPreview(aiUrl: string, id: string, parent: BrowserWindow | null): Promise<AiResult<{ ok: boolean }>> {
  if (!WORK_ID.test(id)) return { ok: false, error: '没有这件作品' }
  const detail = await studio.work(aiUrl, id)
  if (!detail.ok || !detail.data) return { ok: false, error: detail.error ?? '没有这件作品' }
  const w = detail.data
  if (!w.entry.endsWith('.html')) return { ok: false, error: '这件作品不是网页，看它的代码和说明就好' }
  const base = `${aiUrl}/studio/works/${enc(id)}/raw/`
  const ses = session.fromPartition(PREVIEW_PARTITION)
  // 只放行这件作品自己的文件；别的请求（联网、本机别的接口）一律拦下
  ses.webRequest.onBeforeRequest((details, cb) => cb({ cancel: !details.url.startsWith(base) }))
  ses.setPermissionRequestHandler((_wc, _perm, cb) => cb(false))
  const win = new BrowserWindow({
    width: 900,
    height: 700,
    title: `艾拉的作品 · ${w.title}`,
    parent: parent ?? undefined,
    autoHideMenuBar: true,
    backgroundColor: '#101218',
    webPreferences: { partition: PREVIEW_PARTITION, sandbox: true, contextIsolation: true, nodeIntegration: false }
  })
  win.setMenu(null)
  await win.loadURL(base + enc(w.entry))
  return { ok: true, data: { ok: true } }
}

/** 在资源管理器里打开作品文件夹（只认 AI 服务告诉我们的那个） */
export async function revealWork(aiUrl: string, id: string): Promise<AiResult<{ ok: boolean }>> {
  if (!WORK_ID.test(id)) return { ok: false, error: '没有这件作品' }
  const detail = await studio.work(aiUrl, id)
  if (!detail.ok || !detail.data) return { ok: false, error: detail.error ?? '没有这件作品' }
  const folder = detail.data.folder
  // 文件夹得是 …/works/<id>：防止 AI 服务那边给出别的地方
  if (!/[\\/]works[\\/][\w-]{4,64}$/.test(folder) || !folder.endsWith(id)) return { ok: false, error: '作品文件夹不对' }
  const err = await shell.openPath(folder)
  return err ? { ok: false, error: err } : { ok: true, data: { ok: true } }
}
