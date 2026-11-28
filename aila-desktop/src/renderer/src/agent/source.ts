// 工作窗口的数据从哪来：桌面版经主进程转发到 AI 服务；浏览器里调试时经 Vite 代理（/ai）直接请求
import { SseParser } from '../../../shared/chat'
import type { AgentBudgetEvent, AgentCommandList, AgentEngine, AgentEvent, AgentKind, AgentLedger, AgentMemoryNote, AgentMode, AgentSession, AgentSessionSummary, AgentUndoEvent, EngineOption, LocalAgentInfo, ModelGroup, ModelUse, PairPick } from '../../../shared/agent'
import type { AiResult } from '../../../shared/settings'
import type { AcceptanceEvent, DeliveryCard, LineComment, Verdict } from '../../../shared/delivery'
import { bridge } from '../platform/bridge'
import { t } from '../i18n'

export interface ProjectChoice {
  name: string
  path: string
}

export interface AgentSource {
  /** q：按标题或项目路径找；空 = 最近的 */
  list(q?: string): Promise<AgentSessionSummary[]>
  rename(sid: string, title: string): Promise<AgentSessionSummary>
  remove(sid: string): Promise<void>
  /** 自定义命令：这个项目能用的「/名字」 */
  commands?(cwd: string | null, kind: AgentKind): Promise<AgentCommandList>
  /** 项目记忆：按项目目录看、加、删 */
  memory(cwd: string): Promise<AgentMemoryNote[]>
  remember(cwd: string, text: string): Promise<AgentMemoryNote & { added: boolean }>
  forgetMemory(cwd: string, id: string): Promise<void>
  get(sid: string): Promise<AgentSession>
  screenshot?(sid: string, name: string): Promise<string>
  /** model：本机 Agent 用哪个模型（输入框右下角选的；空就用它默认的） */
  create(cwd: string | undefined, mode: AgentMode, isolate?: boolean, budget?: number | null, kind?: AgentKind, engine?: AgentEngine | null, model?: string, collaborators?: PairPick[]): Promise<AgentSessionSummary>
  /** 所有能选的模型，按账号分组（loading 的过一会儿再拿） */
  modelGroups?(use: ModelUse, refresh?: boolean): Promise<ModelGroup[]>
  login?(engine: AgentEngine): Promise<void>
  openInstall?(engine: AgentEngine): Promise<void>
  /** 换别家的模型接着做：开一个新任务，前情交给新的模型 */
  handoff?(sid: string, engine: AgentEngine | null, model: string, label: string): Promise<AgentSessionSummary>
  pair?(sid: string, action: import('../../../shared/agent').PairAction): Promise<{ message: string }>
  /** 本机的 Codex、Claude Code 现在能不能用（新任务里选谁做） */
  localAgents(): Promise<LocalAgentInfo[]>
  /** 本机 Agent 任务的模型、思考深度、快速模式（还没连上时要几秒） */
  engineOptions(sid: string): Promise<EngineOption[]>
  setEngineOption(sid: string, id: string, value: string): Promise<EngineOption[]>
  setBudget(sid: string, amount: number | null): Promise<AgentBudgetEvent>
  /** 在新分支上做时：跑测试、合并回你的代码；返回合并结果（一条 merge 事件） */
  merge(sid: string, runTests: boolean): Promise<AgentEvent>
  discard(sid: string): Promise<AgentEvent>
  undo(sid: string, turnSeq: number): Promise<AgentUndoEvent>
  /** 交付卡：ci=true 时连 PR 上的 CI 一起问（慢一点） */
  delivery(sid: string, ci?: boolean): Promise<DeliveryCard>
  /** 验收：退回修改时服务那边接着开一轮，用 watch 接事件 */
  accept(sid: string, verdict: Verdict, note: string, comments: LineComment[]): Promise<AcceptanceEvent>
  /** 用系统浏览器打开 PR、CI 的链接 */
  openGithub(url: string): Promise<void>
  /** review：/review，只读地审没提交的改动；plan：先出方案（这一轮只读，text 是目标） */
  send(sid: string, text: string, review?: boolean, plan?: boolean): void
  /** 长任务：批准计划并开始做（事件从 onEvent 来）、停下以后接着做、不要这份计划 */
  plan(sid: string, action: 'approve' | 'continue' | 'reject', pauseEach?: boolean): Promise<void>
  watch(sid: string, after: number): void
  interrupt(sid: string): Promise<void>
  setMode(sid: string, mode: AgentMode): Promise<void>
  compact(sid: string): Promise<void>
  /** 上下文账本 */
  context(sid: string): Promise<AgentLedger>
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
  onServiceReady?(cb: () => void): () => void
  onFocus(cb: (sid: string) => void): void
}

function unwrap<T>(r: AiResult<T>): T {
  if (!r.ok) throw new Error(r.error ?? '请求失败')
  return r.data as T
}

function desktop(api: NonNullable<typeof bridge>): AgentSource {
  return {
    list: async (q = '') => unwrap(await api.agentList(q)),
    rename: async (sid, title) => unwrap(await api.agentRename(sid, title)),
    remove: async (sid) => void unwrap(await api.agentDelete(sid)),
    commands: async (cwd, kind) => unwrap(await api.agentCommands(cwd, kind)),
    memory: async (cwd) => unwrap(await api.agentMemory(cwd)),
    remember: async (cwd, text) => unwrap(await api.agentRemember(cwd, text)),
    forgetMemory: async (cwd, id) => void unwrap(await api.agentForgetMemory(cwd, id)),
    get: async (sid) => unwrap(await api.agentGet(sid)),
    screenshot: async (sid, name) => unwrap(await api.agentScreenshot(sid, name)),
    create: async (cwd, mode, isolate, budget, kind = 'project', engine = null, model = '', collaborators = []) => unwrap(await api.agentCreate(cwd, mode, isolate, budget, kind, engine, model, collaborators)),
    modelGroups: async (use, refresh = false) => unwrap(await api.agentModels(use, refresh)),
    handoff: async (sid, engine, model, label) => unwrap(await api.agentHandoff(sid, engine, model, label)),
    pair: async (sid, action) => unwrap(await api.agentPair(sid, action)),
    localAgents: async () => unwrap(await api.agentLocal()),
    login: async (engine) => void unwrap(await api.agentLocalLogin(engine)),
    openInstall: async (engine) => void unwrap(await api.agentLocalOpenInstall(engine)),
    engineOptions: async (sid) => unwrap(await api.agentOptions(sid)),
    setEngineOption: async (sid, id, value) => unwrap(await api.agentSetOption(sid, id, value)),
    setBudget: async (sid, amount) => unwrap(await api.agentSetBudget(sid, amount)),
    merge: async (sid, runTests) => unwrap(await api.agentMerge(sid, runTests)),
    discard: async (sid) => unwrap(await api.agentDiscard(sid)),
    undo: async (sid, turnSeq) => unwrap(await api.agentUndo(sid, turnSeq)),
    delivery: async (sid, ci = false) => unwrap(await api.agentDelivery(sid, ci)),
    accept: async (sid, verdict, note, comments) => unwrap(await api.agentAccept(sid, verdict, note, comments)),
    openGithub: async (url) => void unwrap(await api.agentOpenGithub(url)),
    send: (sid, text, review = false, plan = false) => api.agentSend(sid, text, review, plan),
    plan: async (sid, action, pauseEach = false) => void unwrap(await api.agentPlan(sid, action, pauseEach)),
    watch: (sid, after) => api.agentWatch(sid, after),
    interrupt: async (sid) => void unwrap(await api.agentInterrupt(sid)),
    setMode: async (sid, mode) => void unwrap(await api.agentSetMode(sid, mode)),
    compact: async (sid) => void unwrap(await api.agentCompact(sid)),
    context: async (sid) => unwrap(await api.agentContext(sid)),
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
    onServiceReady: (cb) => api.onServiceState((state) => {
      if (state.phase === 'ready' || state.phase === 'external') cb()
    }),
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
    list: (q = '') => call('GET', `/agent/sessions${q.trim() ? `?q=${encodeURIComponent(q.trim())}` : ''}`),
    rename: (sid, title) => call('PATCH', `/agent/sessions/${encodeURIComponent(sid)}`, { title }),
    remove: async (sid) => void (await call('DELETE', `/agent/sessions/${encodeURIComponent(sid)}`)),
    commands: (cwd, kind) => call('GET', `/agent/commands?${new URLSearchParams({ kind, ...(cwd ? { cwd } : {}) })}`),
    memory: (cwd) => call('GET', `/agent/memory?cwd=${encodeURIComponent(cwd)}`),
    remember: (cwd, text) => call('POST', '/agent/memory', { cwd, text }),
    forgetMemory: async (cwd, id) => void (await call('DELETE', '/agent/memory', { cwd, id })),
    get: (sid) => call('GET', `/agent/sessions/${sid}`),
    create: (cwd, mode, isolate, budget = null, kind = 'project', engine = null, model = '', collaborators = []) => call('POST', '/agent/sessions', {
      cwd, mode, isolate: !!isolate, budget, kind, engine, model, ...(collaborators.length ? { collaborators } : {})
    }),
    modelGroups: (use, refresh = false) => call('GET', `/agent/models?use=${use}${refresh ? '&refresh=true' : ''}`),
    handoff: (sid, engine, model, label) => call('POST', `/agent/sessions/${encodeURIComponent(sid)}/handoff`, { engine, model, label }),
    pair: (sid, action) => call('POST', `/agent/sessions/${encodeURIComponent(sid)}/pair`, action),
    localAgents: () => call('GET', '/agent/local'),
    engineOptions: (sid) => call('GET', `/agent/sessions/${encodeURIComponent(sid)}/options`),
    setEngineOption: (sid, id, value) => call('PUT', `/agent/sessions/${encodeURIComponent(sid)}/options`, { id, value }),
    setBudget: (sid, amount) => call('PUT', `/agent/sessions/${encodeURIComponent(sid)}/budget`, { amount }),
    merge: (sid, runTests) => call('POST', `/agent/sessions/${sid}/merge`, { run_tests: runTests }),
    discard: (sid) => call('POST', `/agent/sessions/${sid}/discard`),
    undo: (sid, turnSeq) => call('POST', `/agent/sessions/${encodeURIComponent(sid)}/undo`, { turn_seq: turnSeq }),
    delivery: (sid, ci = false) => call('GET', `/agent/sessions/${encodeURIComponent(sid)}/delivery?ci=${ci}`),
    accept: (sid, verdict, note, comments) => call('POST', `/agent/sessions/${encodeURIComponent(sid)}/acceptance`, { verdict, note, comments }),
    openGithub: async (url) => void window.open(url, '_blank', 'noopener'),
    send: (sid, text, review = false, plan = false) =>
      void stream(sid, `/agent/sessions/${sid}/messages`, {
        method: 'POST',
        body: JSON.stringify(review ? { text, review: true } : plan ? { text, plan: true } : { text })
      }),
    plan: async (sid, action, pauseEach = false) => {
      if (action === 'reject') return void (await call('POST', `/agent/sessions/${encodeURIComponent(sid)}/plan/reject`))
      const body = action === 'approve' ? JSON.stringify({ pause_each: pauseEach }) : '{}'
      void stream(sid, `/agent/sessions/${encodeURIComponent(sid)}/plan/${action}`, { method: 'POST', body })
    },
    watch: (sid, after) => void stream(sid, `/agent/sessions/${sid}/events?after=${after}`, { method: 'GET' }),
    interrupt: async (sid) => void (await call('POST', `/agent/sessions/${sid}/interrupt`)),
    setMode: async (sid, mode) => void (await call('PUT', `/agent/sessions/${sid}/mode`, { mode })),
    compact: async (sid) => void (await call('POST', `/agent/sessions/${sid}/compact`)),
    context: (sid) => call('GET', `/agent/sessions/${sid}/context`),
    confirm: async (id, approved, remember) =>
      call('POST', '/agent/confirm', { id, approved, remember }).then(
        () => true,
        () => false
      ),
    pickFolder: async () => window.prompt(t('项目文件夹的完整路径（浏览器调试时手动输入）'))?.trim() || null,
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
