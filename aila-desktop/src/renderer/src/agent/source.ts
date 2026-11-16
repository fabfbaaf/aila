// 工作窗口的数据从哪来：桌面版经主进程转发到 AI 服务；浏览器里调试时经 Vite 代理（/ai）直接请求
import { SseParser } from '../../../shared/chat'
import type { AgentEvent, AgentMode, AgentSession, AgentSessionSummary } from '../../../shared/agent'
import type { AiResult } from '../../../shared/settings'
import { bridge } from '../platform/bridge'

export interface ProjectChoice {
  name: string
  path: string
}

export interface AgentSource {
  list(): Promise<AgentSessionSummary[]>
  get(sid: string): Promise<AgentSession>
  create(cwd: string, mode: AgentMode, isolate?: boolean): Promise<AgentSessionSummary>
  /** 在新分支上做时：跑测试、合并回你的代码；返回合并结果（一条 merge 事件） */
  merge(sid: string, runTests: boolean): Promise<AgentEvent>
  discard(sid: string): Promise<AgentEvent>
  send(sid: string, text: string): void
  watch(sid: string, after: number): void
  interrupt(sid: string): Promise<void>
  setMode(sid: string, mode: AgentMode): Promise<void>
  compact(sid: string): Promise<void>
  confirm(id: string, approved: boolean, remember: boolean): Promise<boolean>
  pickFolder(): Promise<string | null>
  takeFocus(): Promise<string | null>
  /** 登记过的代码库（新任务时可以直接选） */
  projects(): Promise<ProjectChoice[]>
  /** 编程用的模型（空 = 和对话一样）、对话的模型 */
  models(): Promise<{ coder: string; chat: string; keySet: boolean } | null>
  openSettings(): void
  onEvent(cb: (sid: string, event: AgentEvent) => void): void
  onStreamEnd(cb: (sid: string, error?: string) => void): void
  onFocus(cb: (sid: string) => void): void
}

function unwrap<T>(r: AiResult<T>): T {
  if (!r.ok) throw new Error(r.error ?? '请求失败')
  return r.data as T
}

function desktop(api: NonNullable<typeof bridge>): AgentSource {
  return {
    list: async () => unwrap(await api.agentList()),
    get: async (sid) => unwrap(await api.agentGet(sid)),
    create: async (cwd, mode, isolate) => unwrap(await api.agentCreate(cwd, mode, isolate)),
    merge: async (sid, runTests) => unwrap(await api.agentMerge(sid, runTests)),
    discard: async (sid) => unwrap(await api.agentDiscard(sid)),
    send: (sid, text) => api.agentSend(sid, text),
    watch: (sid, after) => api.agentWatch(sid, after),
    interrupt: async (sid) => void unwrap(await api.agentInterrupt(sid)),
    setMode: async (sid, mode) => void unwrap(await api.agentSetMode(sid, mode)),
    compact: async (sid) => void unwrap(await api.agentCompact(sid)),
    confirm: (id, approved, remember) => api.agentConfirm(id, approved, remember),
    pickFolder: () => api.agentPickFolder(),
    takeFocus: () => api.agentTakeFocus(),
    projects: async () => {
      const r = await api.codeList()
      return r.ok ? (r.data ?? []).map((p) => ({ name: p.name, path: p.root })) : []
    },
    models: async () => {
      const r = await api.aiSettings()
      if (!r.ok || !r.data) return null
      const llm = r.data.llm
      return { coder: llm.coderModel ?? '', chat: llm.model, keySet: llm.apiKeySet }
    },
    openSettings: () => api.openSettings('model'),
    onEvent: (cb) => void api.onAgentEvent(cb),
    onStreamEnd: (cb) => void api.onAgentStreamEnd(cb),
    onFocus: (cb) => void api.onAgentFocus(cb)
  }
}

function web(): AgentSource {
  const eventCbs: ((sid: string, e: AgentEvent) => void)[] = []
  const endCbs: ((sid: string, error?: string) => void)[] = []
  const call = async <T>(method: string, path: string, body?: unknown): Promise<T> => {
    const res = await fetch(`/ai${path}`, {
      method,
      headers: body === undefined ? undefined : { 'content-type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body)
    })
    if (!res.ok) {
      const detail = await res.json().then((b: { detail?: string }) => b.detail).catch(() => '')
      throw new Error(detail || `AI 服务返回 ${res.status}`)
    }
    return (await res.json()) as T
  }
  const stream = async (sid: string, path: string, init: RequestInit): Promise<void> => {
    let error: string | undefined
    try {
      const res = await fetch(`/ai${path}`, { ...init, headers: { 'content-type': 'application/json' } })
      if (!res.ok || !res.body) {
        error = `AI 服务返回 ${res.status}`
        return
      }
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      const parser = new SseParser((data) => {
        const e = JSON.parse(data) as AgentEvent
        for (const cb of eventCbs) cb(sid, e)
      })
      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        parser.feed(decoder.decode(value, { stream: true }))
      }
    } catch (err) {
      error = (err as Error).message
    } finally {
      for (const cb of endCbs) cb(sid, error)
    }
  }
  return {
    list: () => call('GET', '/agent/sessions'),
    get: (sid) => call('GET', `/agent/sessions/${sid}`),
    create: (cwd, mode, isolate) => call('POST', '/agent/sessions', { cwd, mode, isolate: !!isolate }),
    merge: (sid, runTests) => call('POST', `/agent/sessions/${sid}/merge`, { run_tests: runTests }),
    discard: (sid) => call('POST', `/agent/sessions/${sid}/discard`),
    send: (sid, text) => void stream(sid, `/agent/sessions/${sid}/messages`, { method: 'POST', body: JSON.stringify({ text }) }),
    watch: (sid, after) => void stream(sid, `/agent/sessions/${sid}/events?after=${after}`, { method: 'GET' }),
    interrupt: async (sid) => void (await call('POST', `/agent/sessions/${sid}/interrupt`)),
    setMode: async (sid, mode) => void (await call('PUT', `/agent/sessions/${sid}/mode`, { mode })),
    compact: async (sid) => void (await call('POST', `/agent/sessions/${sid}/compact`)),
    confirm: async (id, approved, remember) =>
      call('POST', '/agent/confirm', { id, approved, remember }).then(
        () => true,
        () => false
      ),
    pickFolder: async () => window.prompt('项目文件夹的完整路径（浏览器调试时手动输入）')?.trim() || null,
    takeFocus: async () => null,
    projects: async () =>
      (await call<{ name: string; root: string }[]>('GET', '/code/projects').catch(() => [])).map((p) => ({ name: p.name, path: p.root })),
    models: async () => {
      const s = await call<{ llm: { model: string; coderModel?: string; apiKeySet: boolean } }>('GET', '/settings').catch(() => null)
      return s ? { coder: s.llm.coderModel ?? '', chat: s.llm.model, keySet: s.llm.apiKeySet } : null
    },
    openSettings: () => console.info('[工作窗口] 打开设置 → 对话'),
    onEvent: (cb) => void eventCbs.push(cb),
    onStreamEnd: (cb) => void endCbs.push(cb),
    onFocus: () => {}
  }
}

export function createAgentSource(): AgentSource {
  return bridge ? desktop(bridge) : web()
}
