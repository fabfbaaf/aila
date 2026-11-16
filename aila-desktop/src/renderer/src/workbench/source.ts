// 星图的数据从哪来：桌面版经主进程转发到 AI 服务；浏览器里调试时经 Vite 代理（/ai）直接请求
import type { AiResult, CodeProject } from '../../../shared/settings'
import type { AgentActivity } from '../../../shared/agent'
import type { ChunkSource, CodeGraph, LocatedError, OutlineItem, PendingFix, WorkbenchFocus } from '../../../shared/workbench'
import { bridge } from '../platform/bridge'

export interface StarSource {
  projects(): Promise<CodeProject[]>
  graph(project: string): Promise<CodeGraph>
  outline(project: string, file: string): Promise<OutlineItem[]>
  chunk(project: string, file: string, line: number): Promise<ChunkSource | null>
  locate(text: string): Promise<LocatedError | null>
  /** 让桌面上的她说这句话（浏览器调试时只打印） */
  say(text: string): void
  openSettings(): void
  /** 打开星图时要定位的报错 */
  takeFocus(): Promise<WorkbenchFocus | null>
  onFocus(cb: (focus: WorkbenchFocus) => void): void
  /** 她准备好、正等用户点头的代码修改 */
  pendingFix(): Promise<PendingFix | null>
  onPendingFix(cb: (fix: PendingFix | null) => void): void
  /** 她在哪个文件上干活（工作窗口里的任务） */
  onActivity(cb: (a: AgentActivity) => void): void
  /** 双手合十确认这份修改；false：它已经不在等了 */
  seal(id: string): Promise<boolean>
  /** 把修报错交给工作窗口里的编程 Agent（开会话、开始做、打开工作窗口）；失败时返回原因 */
  task(project: string, text: string): Promise<string | null>
}

function unwrap<T>(r: AiResult<T>): T {
  if (!r.ok) throw new Error(r.error ?? '请求失败')
  return r.data as T
}

function desktop(api: NonNullable<typeof bridge>): StarSource {
  return {
    projects: async () => unwrap(await api.codeList()),
    graph: async (p) => unwrap(await api.codeGraph(p)),
    outline: async (p, f) => unwrap(await api.codeOutline(p, f)),
    chunk: async (p, f, l) => {
      const r = await api.codeChunk(p, f, l)
      return r.ok ? (r.data ?? null) : null
    },
    locate: async (t) => unwrap(await api.codeLocateAll(t)),
    say: (t) => api.sayToAila(t),
    openSettings: () => api.openSettings('code'),
    takeFocus: () => api.workbenchTakeFocus(),
    onFocus: (cb) => void api.onWorkbenchFocus(cb),
    pendingFix: () => api.pendingFix(),
    onPendingFix: (cb) => void api.onPendingFix(cb),
    onActivity: (cb) => void api.onWorkbenchActivity(cb),
    seal: (id) => api.gestureSeal(id),
    task: async (project, text) => {
      const r = await api.agentTask({ project, text })
      return r.ok ? null : (r.error ?? '没开成')
    }
  }
}

function web(): StarSource {
  const get = async <T>(path: string): Promise<T> => {
    const res = await fetch(`/ai${path}`)
    if (!res.ok) throw new Error(`AI 服务返回 ${res.status}`)
    return (await res.json()) as T
  }
  const enc = encodeURIComponent
  return {
    projects: () => get('/code/projects'),
    graph: (p) => get(`/code/projects/${enc(p)}/graph`),
    outline: (p, f) => get(`/code/projects/${enc(p)}/outline?file=${enc(f)}`),
    chunk: (p, f, l) => get<ChunkSource>(`/code/projects/${enc(p)}/chunk?file=${enc(f)}&line=${l}`).catch(() => null),
    locate: async (text) => {
      const res = await fetch('/ai/code/locate', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ text })
      })
      return (await res.json()) as LocatedError | null
    },
    say: (t) => console.info('[星图] 想让她说：', t),
    openSettings: () => console.info('[星图] 打开设置 → 代码库'),
    takeFocus: async () => null,
    onFocus: () => {},
    pendingFix: async () => null,
    onPendingFix: () => {},
    onActivity: () => {},
    seal: async (id) => {
      console.info('[星图] 结印确认：', id)
      return true
    },
    task: async (project, text) => {
      const res = await fetch('/ai/agent/tasks', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ project, text })
      })
      return res.ok ? null : `AI 服务返回 ${res.status}`
    }
  }
}

export function createSource(): StarSource {
  return bridge ? desktop(bridge) : web()
}
