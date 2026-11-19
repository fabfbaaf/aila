// 作品页的数据从哪来：桌面版经主进程（只认主窗口）转到 AI 服务；浏览器里调试时经 Vite 代理（/ai）直接请求
import type { DiaryEntry, Work, WorkDetail } from '../../../shared/studio'
import type { AiResult } from '../../../shared/settings'
import { bridge } from '../platform/bridge'

export interface StudioSource {
  works(): Promise<Work[]>
  work(id: string): Promise<WorkDetail>
  remove(id: string): Promise<void>
  /** 打开网页作品（桌面版：单独的沙盒窗口；浏览器里：新标签页） */
  open(id: string, entry: string): Promise<void>
  /** 打开作品文件夹（浏览器里做不到，没有） */
  reveal?(id: string): Promise<void>
  /** 现在就让她做一件；可以给个点子 */
  now(idea?: { title: string; idea: string; kind: 'web' | 'script' }): Promise<Work>
  diary(): Promise<DiaryEntry[]>
  /** 认识第几天（桌面版才有） */
  known(): Promise<{ firstSeen?: string; days: number } | null>
  /** 看她做这件作品的过程：打开任务页里那个会话 */
  openSession?(sid: string): void
}

function unwrap<T>(r: AiResult<T>): T {
  if (!r.ok) throw new Error(r.error ?? '请求失败')
  return r.data as T
}

function desktop(api: NonNullable<typeof bridge>): StudioSource {
  return {
    works: async () => unwrap(await api.studioList()),
    work: async (id) => unwrap(await api.studioGet(id)),
    remove: async (id) => void unwrap(await api.studioDelete(id)),
    open: async (id) => void unwrap(await api.studioOpen(id)),
    reveal: async (id) => void unwrap(await api.studioReveal(id)),
    now: async (idea) => unwrap(await api.studioNow(idea)),
    diary: async () => unwrap(await api.studioDiary()),
    known: () => api.studioKnown(),
    openSession: (sid) => api.openAgent(sid)
  }
}

function web(): StudioSource {
  const call = async <T>(method: string, path: string, body?: unknown): Promise<T> => {
    const res = await fetch(`/ai${path}`, {
      method,
      ...(body === undefined ? {} : { headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) })
    })
    const data = (await res.json().catch(() => ({}))) as T & { detail?: unknown }
    if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `AI 服务返回 ${res.status}`)
    return data
  }
  const enc = encodeURIComponent
  return {
    works: () => call('GET', '/studio/works'),
    work: (id) => call('GET', `/studio/works/${enc(id)}`),
    remove: async (id) => void (await call('DELETE', `/studio/works/${enc(id)}`)),
    open: async (id, entry) => void window.open(`/ai/studio/works/${enc(id)}/raw/${enc(entry)}`, '_blank', 'noopener'),
    now: (idea) => call('POST', '/studio/works', idea ? { idea: { ...idea, why: '' } } : {}),
    diary: () => call('GET', '/studio/diary'),
    known: async () => null
  }
}

export function createStudioSource(): StudioSource {
  return bridge ? desktop(bridge) : web()
}
