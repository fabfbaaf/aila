// 工作窗口（编程 Agent）：主进程转发 AI 服务的 /agent 接口。
// 事件流由主进程收、再发给工作窗口，渲染层不用放开 CSP；同时：
// - 把进展（开始、要确认、做完）告诉桌面上的她
// - 把她在哪个文件上干活告诉星图（读的星球闪一下，改过的变绿）
// - 等你确认的改文件操作交给星图显示（双手合十也能确认）
// 工作窗口关了，正在做的任务照样做完（AI 服务那边在做），做完她会说一声。
import { SseParser } from '../shared/chat'
import { isTestCommand } from '../shared/agent'
import type {
  AgentActivity,
  AgentBudgetEvent,
  AgentCommandList,
  AgentEngine,
  AgentEvent,
  AgentKind,
  AgentLedger,
  AgentMemoryNote,
  AgentMode,
  AgentProgress,
  AgentSession,
  AgentSessionSummary,
  PairPick,
  AgentUndoEvent,
  ComputerContext,
  EngineOption,
  LocalAgentInfo,
  ModelGroup,
  ModelUse
} from '../shared/agent'
import type { AiResult } from '../shared/settings'
import type { ClaudeQuotaConfig, ProviderQuota, QuotaRecord } from '../shared/providerQuotas'
import type { McpPreviewItem, McpServerInfo } from '../shared/mcp'
import type { AcceptanceEvent, DeliveryCard, LineComment, Verdict } from '../shared/delivery'
import type { CatalogListing, Companion, ExtensionList, ExtensionPlan, ExtensionUpdate } from '../shared/extensions'
import { tm } from './lang'
import { taskWorkRoute, type TaskWorkRequest, type TaskWorkResponses } from '../shared/taskWork'

export interface RelayHooks {
  /** 一个事件 → 工作窗口 */
  emit: (sid: string, event: AgentEvent) => void
  /** 事件流结束（出错时带原因） */
  ended: (sid: string, error?: string) => void
  /** 进展 → 桌面上的她 */
  progress: (p: AgentProgress) => void
  /** 她在哪个文件上干活 → 星图 */
  activity: (a: AgentActivity) => void
  /** 等你确认的改文件操作（null：不等了）→ 星图 */
  confirm: (c: { sid: string; id: string; detail: string } | null) => void
}

const READ_TOOLS = new Set(['read_file'])
const EDIT_TOOLS = new Set(['edit_file', 'write_file'])

export class AgentRelay {
  private streams = new Map<string, AbortController>()
  private turnFiles = new Map<string, Set<string>>()
  /** 会话 → 项目目录（列表、打开、新建时记下） */
  private cwds = new Map<string, string>()
  /** 工具调用 → 它碰的文件 */
  private callFiles = new Map<string, string>()
  /** 她发出去的哪些命令是在跑测试（sid:调用 id） */
  private testCalls = new Set<string>()
  /** 每个会话连着几次测试没过 */
  private testStreak = new Map<string, number>()
  /** 正在按计划做的会话（这一轮是某个阶段） */
  private planTurns = new Set<string>()
  /** 正在做的阶段叫什么（阶段做完时她说一句用） */
  private stageTitles = new Map<string, string>()
  private pendingConfirm: { sid: string; id: string } | null = null

  constructor(
    private aiUrl: string,
    private hooks: RelayHooks
  ) {}

  /** q：按标题或项目路径找（服务端先筛再限数量，能找到很早以前的会话） */
  async list(q = ''): Promise<AiResult<AgentSessionSummary[]>> {
    const query = q.trim() ? `?q=${encodeURIComponent(q.trim())}` : ''
    const r = await this.call<AgentSessionSummary[]>('GET', `/agent/sessions${query}`)
    for (const s of r.data ?? []) this.cwds.set(s.id, s.cwd)
    return r
  }

  async get(sid: string): Promise<AiResult<AgentSession>> {
    return this.remember(await this.call<AgentSession>('GET', `/agent/sessions/${encodeURIComponent(sid)}`))
  }

  /** model：保存用户选的模型，连接 Agent 后再生效。 */
  async create(cwd: string | undefined, mode: AgentMode, isolate = false, budget: number | null = null, kind: AgentKind = 'project',
    engine: AgentEngine | null = null, model = '', collaborators: PairPick[] = []): Promise<AiResult<AgentSessionSummary>> {
    return this.remember(await this.call<AgentSessionSummary>('POST', '/agent/sessions', {
      cwd, mode, isolate, budget, kind, engine, model, ...(collaborators.length ? { collaborators } : {})
    }))
  }

  /** 换别家的模型接着做：开一个新任务，前情交给新的模型 */
  async handoff(sid: string, engine: AgentEngine | null, model: string, label: string): Promise<AiResult<AgentSessionSummary>> {
    return this.remember(await this.call<AgentSessionSummary>('POST', `/agent/sessions/${encodeURIComponent(sid)}/handoff`,
      { engine, model, label }, 120000))
  }

  /** 所有能选的模型，按账号分组（本机 Agent 的模型在后台问，loading 时过一会儿再拿） */
  models(use: ModelUse, refresh = false): Promise<AiResult<ModelGroup[]>> {
    return this.call('GET', `/agent/models?use=${use}${refresh ? '&refresh=true' : ''}`, undefined, 60000)
  }

  taskWork<R extends TaskWorkRequest>(request: R): Promise<AiResult<TaskWorkResponses[R['action']]>> {
    const route = taskWorkRoute(request)
    if (!route) return Promise.resolve({ ok: false, error: tm('请求失败') })
    return this.call(route.method, route.path, route.body, 120000)
  }

  /** 提交用户对分工的决定，或启动联合验收。 */
  pair(sid: string, action: import('../shared/agent').PairAction): Promise<AiResult<{ message: string }>> {
    return this.call('POST', `/agent/sessions/${encodeURIComponent(sid)}/pair`, action, 60000)
  }

  /** 检查本机 Agent 的程序和登录状态。 */
  localAgents(): Promise<AiResult<LocalAgentInfo[]>> {
    return this.call('GET', '/agent/local', undefined, 60000)
  }

  loginCommand(engine: AgentEngine): Promise<AiResult<{ command: string[]; profile?: { key: string; path: string } }>> {
    return this.call('GET', `/agent/local/${engine}/login`, undefined, 30000)
  }

  providerQuotas(refresh: boolean): Promise<AiResult<ProviderQuota[]>> {
    return this.call('GET', refresh ? '/agent/quotas?refresh=true' : '/agent/quotas', undefined, 45000)
  }

  recordProviderQuota(body: QuotaRecord): Promise<AiResult<ProviderQuota[]>> {
    return this.call('POST', '/agent/quotas/record', body)
  }

  claudeQuotaConfig(): Promise<AiResult<ClaudeQuotaConfig>> {
    return this.call('POST', '/agent/quotas/claude')
  }

  linkCursorQuota(linked: boolean): Promise<AiResult<ProviderQuota[]>> {
    return this.call('PUT', '/agent/quotas/cursor', { linked })
  }

  /** 本机 Agent 的模型、思考深度、快速模式（还没连上时它要先起来，几秒） */
  engineOptions(sid: string): Promise<AiResult<EngineOption[]>> {
    return this.call('GET', `/agent/sessions/${encodeURIComponent(sid)}/options`, undefined, 120000)
  }

  setEngineOption(sid: string, id: string, value: string): Promise<AiResult<EngineOption[]>> {
    return this.call('PUT', `/agent/sessions/${encodeURIComponent(sid)}/options`, { id, value }, 60000)
  }

  /** 装 ACP 适配器（npm，要几十秒） */
  installAdapters(): Promise<AiResult<{ message: string; agents: LocalAgentInfo[] }>> {
    return this.call('POST', '/agent/local/install', {}, 330000)
  }

  computerContext(): Promise<AiResult<ComputerContext>> {
    return this.call('GET', '/agent/computer/context')
  }

  setBudget(sid: string, amount: number | null): Promise<AiResult<AgentBudgetEvent>> {
    return this.call('PUT', `/agent/sessions/${encodeURIComponent(sid)}/budget`, { amount })
  }

  /** 在新分支上做时：跑测试、合并回你的代码（测试可能要跑几分钟） */
  merge(sid: string, runTests: boolean): Promise<AiResult<AgentEvent>> {
    return this.call('POST', `/agent/sessions/${encodeURIComponent(sid)}/merge`, { run_tests: runTests }, 660000)
  }

  /** 放弃分支（你的代码保持原样） */
  discard(sid: string): Promise<AiResult<AgentEvent>> {
    return this.call('POST', `/agent/sessions/${encodeURIComponent(sid)}/discard`, undefined, 60000)
  }

  /** 交付卡（改动、测试、PR、CI 都是服务那边代码算的）；ci=false 不问 CI，快 */
  delivery(sid: string, ci: boolean): Promise<AiResult<DeliveryCard>> {
    return this.call('GET', `/agent/sessions/${encodeURIComponent(sid)}/delivery?ci=${ci}`, undefined, ci ? 45000 : 20000)
  }

  /** 验收：通过只记一笔；退回修改记一笔，服务那边接着开一轮（事件用 watch 接） */
  accept(sid: string, verdict: Verdict, note: string, comments: LineComment[]): Promise<AiResult<AcceptanceEvent>> {
    return this.call('POST', `/agent/sessions/${encodeURIComponent(sid)}/acceptance`, { verdict, note, comments })
  }

  /** 用用户确认过的轮次发请求，期间有新的一轮就由服务拒绝。 */
  undo(sid: string, turnSeq: number): Promise<AiResult<AgentUndoEvent>> {
    return this.call('POST', `/agent/sessions/${encodeURIComponent(sid)}/undo`, { turn_seq: turnSeq }, 60000)
  }

  /** 改名只追加标题事件，历史和模型消息保持原样 */
  async rename(sid: string, title: string): Promise<AiResult<AgentSessionSummary>> {
    return this.remember(await this.call<AgentSessionSummary>('PATCH', `/agent/sessions/${encodeURIComponent(sid)}`, { title }))
  }

  /** 服务端停掉后台进程、放弃分支并删日志后，再清理本地订阅 */
  async delete(sid: string): Promise<AiResult<{ ok: boolean }>> {
    const result = await this.call<{ ok: boolean }>('DELETE', `/agent/sessions/${encodeURIComponent(sid)}`, undefined, 60000)
    if (result.ok) {
      this.streams.get(sid)?.abort()
      this.streams.delete(sid)
      this.cwds.delete(sid)
      this.turnFiles.delete(sid)
      for (const key of this.callFiles.keys()) if (key.startsWith(`${sid}:`)) this.callFiles.delete(key)
      if (this.pendingConfirm?.sid === sid) this.clearPending()
    }
    return result
  }

  /** 自定义命令：这个项目能用的「/名字」（电脑任务只有自己的） */
  commands(cwd: string | null, kind: AgentKind): Promise<AiResult<AgentCommandList>> {
    const q = new URLSearchParams({ kind, ...(cwd ? { cwd } : {}) })
    return this.call('GET', `/agent/commands?${q}`)
  }

  /** 项目记忆：按项目目录看、加、删（改动从下一个会话起放进开头） */
  memory(cwd: string): Promise<AiResult<AgentMemoryNote[]>> {
    return this.call('GET', `/agent/memory?cwd=${encodeURIComponent(cwd)}`)
  }

  addMemory(cwd: string, text: string): Promise<AiResult<AgentMemoryNote & { added: boolean }>> {
    return this.call('POST', '/agent/memory', { cwd, text })
  }

  forgetMemory(cwd: string, id: string): Promise<AiResult<{ ok: boolean }>> {
    return this.call('DELETE', '/agent/memory', { cwd, id })
  }

  /** MCP 外部工具服务：状态、先看一眼、保存并启动、重启、停用、删除、撤销「以后都不用问」 */
  mcp(): Promise<AiResult<McpServerInfo[]>> {
    return this.call('GET', '/agent/mcp', undefined, 30000)
  }

  mcpPreview(json: string): Promise<AiResult<McpPreviewItem[]>> {
    return this.call('POST', '/agent/mcp/preview', { json })
  }

  /** 启动要等服务起来（npx 第一次会下载），给长一点 */
  mcpImport(json: string): Promise<AiResult<McpServerInfo[]>> {
    return this.call('POST', '/agent/mcp/import', { json }, 180000)
  }

  mcpRestart(name: string): Promise<AiResult<McpServerInfo[]>> {
    return this.call('POST', `/agent/mcp/${encodeURIComponent(name)}/restart`, undefined, 120000)
  }

  mcpDisable(name: string, disabled: boolean): Promise<AiResult<McpServerInfo[]>> {
    return this.call('PUT', `/agent/mcp/${encodeURIComponent(name)}/disabled`, { disabled }, 120000)
  }

  // ----- 扩展包（选文件在主进程，这里只转发） -----

  extList(): Promise<AiResult<ExtensionList>> {
    return this.call('GET', '/extensions', undefined, 30000)
  }

  extInspect(path: string): Promise<AiResult<{ token: string; plan: ExtensionPlan }>> {
    return this.call('POST', '/extensions/inspect', { path }, 60000)
  }

  /** 装好会按新配置补开工具服务（默认停着，通常很快），给长一点 */
  extInstall(token: string): Promise<AiResult<ExtensionList & { installed: string }>> {
    return this.call('POST', '/extensions/install', { token }, 120000)
  }

  extDiscard(token: string): Promise<AiResult<{ ok: boolean }>> {
    return this.call('POST', '/extensions/discard', { token })
  }

  /** 打开工具服务要等它起来（npx 第一次会下载） */
  extUpdate(id: string, update: ExtensionUpdate): Promise<AiResult<ExtensionList>> {
    return this.call('PATCH', `/extensions/${encodeURIComponent(id)}`, update, 180000)
  }

  extRemove(id: string): Promise<AiResult<ExtensionList>> {
    return this.call('DELETE', `/extensions/${encodeURIComponent(id)}`, undefined, 60000)
  }

  /** 桌面伙伴用：动作、台词、能换的形象 */
  extCompanion(): Promise<AiResult<Companion>> {
    return this.call('GET', '/extensions/companion')
  }

  /** 目录要联网读，给长一点 */
  extCatalog(): Promise<AiResult<CatalogListing>> {
    return this.call('GET', '/extensions/catalog', undefined, 60000)
  }

  extCatalogSources(urls: string[]): Promise<AiResult<CatalogListing>> {
    return this.call('PUT', '/extensions/catalogs', { urls }, 60000)
  }

  /** 下载扩展包（最大 50MB）再检查 */
  extCatalogInspect(catalog: string, id: string): Promise<AiResult<{ token: string; plan: ExtensionPlan }>> {
    return this.call('POST', '/extensions/catalog/inspect', { catalog, id }, 300000)
  }

  mcpRemove(name: string): Promise<AiResult<McpServerInfo[]>> {
    return this.call('DELETE', `/agent/mcp/${encodeURIComponent(name)}`, undefined, 30000)
  }

  mcpForget(name: string, tool: string): Promise<AiResult<McpServerInfo[]>> {
    return this.call('DELETE', `/agent/mcp/${encodeURIComponent(name)}/allow`, { tool })
  }

  /** 开一个会话并马上开始做（屏幕上的报错、星图交过来的任务） */
  async task(task: { text: string; project?: string; cwd?: string }): Promise<AiResult<AgentSessionSummary>> {
    return this.remember(await this.call<AgentSessionSummary>('POST', '/agent/tasks', task))
  }

  interrupt(sid: string): Promise<AiResult<unknown>> {
    return this.call('POST', `/agent/sessions/${encodeURIComponent(sid)}/interrupt`)
  }

  setMode(sid: string, mode: AgentMode): Promise<AiResult<unknown>> {
    return this.call('PUT', `/agent/sessions/${encodeURIComponent(sid)}/mode`, { mode })
  }

  /** 上下文账本 */
  context(sid: string): Promise<AiResult<AgentLedger>> {
    return this.call('GET', `/agent/sessions/${encodeURIComponent(sid)}/context`)
  }

  compact(sid: string): Promise<AiResult<unknown>> {
    return this.call('POST', `/agent/sessions/${encodeURIComponent(sid)}/compact`, undefined, 180000)
  }

  async confirm(id: string, approved: boolean, remember: boolean): Promise<boolean> {
    return (await this.call('POST', '/agent/confirm', { id, approved, remember })).ok
  }

  /** 星图里双手合十：确认正等着的那个改文件操作（id 要对得上） */
  async approvePending(id: string): Promise<boolean> {
    if (this.pendingConfirm?.id !== id) return false
    return this.confirm(id, true, false)
  }

  /** 说一句话，开始一轮；事件陆续发给工作窗口 */
  /** review：/review，只读地审没提交的改动（text 是可选的「特别注意什么」）；plan：先出方案（这一轮只读，text 是目标） */
  send(sid: string, text: string, review = false, plan = false): void {
    const body = review ? { text, review: true } : plan ? { text, plan: true } : { text }
    void this.stream(sid, `/agent/sessions/${encodeURIComponent(sid)}/messages`, { method: 'POST', body: JSON.stringify(body) })
  }

  /** 长任务：批准计划并开始按阶段做 / 停下以后接着做（事件流一直到计划停下）/ 不要这份计划 */
  planApprove(sid: string, pauseEach: boolean): void {
    void this.stream(sid, `/agent/sessions/${encodeURIComponent(sid)}/plan/approve`, { method: 'POST', body: JSON.stringify({ pause_each: pauseEach }) })
  }

  /** redo：先把这一阶段改过的文件退回到上一阶段做完时，再从头做 */
  planContinue(sid: string, redo = false): void {
    void this.stream(sid, `/agent/sessions/${encodeURIComponent(sid)}/plan/continue`, { method: 'POST', body: JSON.stringify(redo ? { redo: true } : {}) })
  }

  planReject(sid: string): Promise<AiResult<AgentEvent>> {
    return this.call('POST', `/agent/sessions/${encodeURIComponent(sid)}/plan/reject`)
  }

  /** 接上一个会话：补发 after 之后的事件，正在做的话接着推（之后的进展她也会汇报） */
  watch(sid: string, after: number): void {
    void this.stream(sid, `/agent/sessions/${encodeURIComponent(sid)}/events?after=${after}`, { method: 'GET' })
  }

  stopAll(): void {
    for (const c of this.streams.values()) c.abort()
    this.streams.clear()
  }

  private remember<T extends { id: string; cwd: string }>(r: AiResult<T>): AiResult<T> {
    if (r.ok && r.data) this.cwds.set(r.data.id, r.data.cwd)
    return r
  }

  private async stream(sid: string, path: string, init: RequestInit): Promise<void> {
    this.streams.get(sid)?.abort()
    const controller = new AbortController()
    this.streams.set(sid, controller)
    let error: string | undefined
    try {
      const res = await fetch(`${this.aiUrl}${path}`, {
        ...init,
        headers: { 'content-type': 'application/json' },
        signal: controller.signal
      })
      if (!res.ok || !res.body) {
        error = await detail(res)
        return
      }
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      const parser = new SseParser((data) => {
        let event: AgentEvent
        try {
          event = JSON.parse(data) as AgentEvent
        } catch {
          return
        }
        this.hooks.emit(sid, event)
        this.track(sid, event)
      })
      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        parser.feed(decoder.decode(value, { stream: true }))
      }
    } catch (err) {
      if (!controller.signal.aborted) error = tm('连不上 AI 服务：{why}', { why: err instanceof Error ? err.message : String(err) })
    } finally {
      if (this.streams.get(sid) === controller) this.streams.delete(sid)
      if (!controller.signal.aborted) this.hooks.ended(sid, error)
    }
  }

  /** 这一轮的进展 → 桌面上的她；在哪个文件上干活 → 星图 */
  private track(sid: string, e: AgentEvent): void {
    const cwd = this.cwds.get(sid) ?? ''
    switch (e.type) {
      case 'user':
        this.turnFiles.set(sid, new Set())
        // 按计划做的那几轮：做完一轮不说「做完了」，等阶段做完、整个计划做完再说
        if (typeof e.plan_stage === 'number') {
          this.planTurns.add(sid)
          this.stageTitles.set(sid, String(e.title ?? '').replace(/^.*?阶段：/, ''))
        } else this.planTurns.delete(sid)
        this.hooks.progress({ kind: 'start', title: String((e.title ?? e.text) ?? '').slice(0, 40) })
        break
      case 'assistant':
        for (const c of (e.tool_calls as { id: string; name: string; arguments: string }[]) ?? []) {
          if (c.name === 'run_command') {
            try {
              const command = String((JSON.parse(c.arguments) as { command?: unknown }).command ?? '')
              if (isTestCommand(command)) this.testCalls.add(`${sid}:${c.id}`)
            } catch {
              // 参数不是 JSON：不管
            }
            continue
          }
          if (!READ_TOOLS.has(c.name) && !EDIT_TOOLS.has(c.name)) continue
          try {
            const path = String((JSON.parse(c.arguments) as { path?: unknown }).path ?? '')
            if (path) this.callFiles.set(`${sid}:${c.id}`, path.replace(/\\/g, '/'))
          } catch {
            // 参数不是 JSON：不管
          }
        }
        break
      case 'tool_start': {
        const file = this.callFiles.get(`${sid}:${String(e.id)}`)
        if (file && READ_TOOLS.has(String(e.name))) this.hooks.activity({ sid, cwd, file, kind: 'read' })
        break
      }
      case 'confirm':
        this.hooks.progress({ kind: 'confirm', action: String(e.action ?? ''), sid })
        if (e.kind === 'edit') {
          this.pendingConfirm = { sid, id: String(e.id) }
          this.hooks.confirm({ sid, id: String(e.id), detail: String(e.detail ?? '') })
        }
        break
      case 'confirm_end':
        if (this.pendingConfirm?.id === e.id) this.clearPending()
        break
      case 'tool':
        this.callFiles.delete(`${sid}:${String(e.id)}`)
        // 跑的是测试：过了高兴一下（之前一直没过的话说一句），连着没过就叹口气、给你打打气
        if (this.testCalls.delete(`${sid}:${String(e.id)}`) && e.status !== 'skipped') {
          const ok = e.status === 'ok'
          const before = this.testStreak.get(sid) ?? 0
          const streak = ok ? 0 : before + 1
          this.testStreak.set(sid, streak)
          this.hooks.progress({ kind: 'tests', ok, streak, recovered: ok && before > 0 })
        }
        if (e.status === 'ok' && typeof e.file === 'string') {
          this.turnFiles.get(sid)?.add(e.file)
          this.hooks.activity({ sid, cwd, file: e.file, kind: 'edit' })
        }
        if (e.name === 'stage_done' && e.status === 'ok') this.hooks.progress({ kind: 'stage', title: this.stageTitles.get(sid) ?? '' })
        break
      case 'plan_end':
        this.planTurns.delete(sid)
        this.hooks.progress({ kind: 'plan_end' })
        break
      case 'plan_pause':
        this.planTurns.delete(sid)
        this.hooks.progress({ kind: 'plan_pause', text: String(e.text ?? '') })
        break
      case 'turn_end': {
        if (this.pendingConfirm?.sid === sid) this.clearPending()
        const files = this.turnFiles.get(sid)?.size ?? 0
        this.turnFiles.delete(sid)
        if (e.reason === 'done' && this.planTurns.has(sid)) this.hooks.progress({ kind: 'stopped', reason: 'plan' })
        else if (e.reason === 'done') this.hooks.progress({ kind: 'done', files })
        else if (e.reason === 'budget') this.hooks.progress({ kind: 'budget' })
        else if (e.reason === 'error') this.hooks.progress({ kind: 'error', message: String(e.error ?? '') })
        else this.hooks.progress({ kind: 'stopped', reason: String(e.reason ?? '') })
        break
      }
    }
  }

  private clearPending(): void {
    this.pendingConfirm = null
    this.hooks.confirm(null)
  }

  private async call<T>(method: string, path: string, body?: unknown, timeout = 15000): Promise<AiResult<T>> {
    try {
      const res = await fetch(`${this.aiUrl}${path}`, {
        method,
        headers: body === undefined ? undefined : { 'content-type': 'application/json' },
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: AbortSignal.timeout(timeout)
      })
      if (!res.ok) return { ok: false, error: await detail(res) }
      return { ok: true, data: (await res.json()) as T }
    } catch (err) {
      return { ok: false, error: tm('连不上 AI 服务：{why}', { why: err instanceof Error ? err.message : String(err) }) }
    }
  }
}

async function detail(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as { detail?: unknown }
    if (typeof body.detail === 'string') return body.detail
  } catch {
    // 不是 JSON
  }
  return tm('AI 服务返回 {status}', { status: res.status })
}
