// 任务管理用的请求和回包。桌面 IPC 和浏览器调试都只映射到这几个固定接口。
import type { AgentEngine } from './agent'

export type NativeEngine = 'codex' | 'codex-alt' | 'claude' | 'claude-alt'
export type WorkModel = { engine: AgentEngine | null; model: string }
export type WorkQueue = {
  id: string; title: string; project: string; sid: string; error: string; attempts: string[]
  state: 'queued' | 'starting' | 'running' | 'review' | 'stopped' | 'canceled' | 'accepted'
  waiting: { id: string; title: string; status: 'pending' | 'rejected' | 'canceled' | 'missing' }[]
}
export type WorkGroup = {
  id: string; title: string; contract: string; acceptance: string; integration: string | null
  members: { sid: string | null; repo: string; branch: string; worktree: string; outcome?: string | null }[]
  integration_result?: { outcome: string | null; commands: { id: string; command: string; exit_code: number | null }[] } | null
}
export type WorkRecommendation = {
  project: string; limit: number
  models: { engine: AgentEngine | 'aila'; model: string; samples: number; passed: number; seconds: number; rework: number }[]
}
export type NativeSession = { id: string; title: string; cwd: string; updated: string | null }
export type NativeImport = { sid: string; cwd: string; branch: string; changes: string[]; messages: number }
export type TaskWorkRequest =
  | { action: 'state' }
  | { action: 'models'; cwd: string }
  | { action: 'limit'; cwd: string; limit: number }
  | { action: 'queue'; cwd: string; title: string; prompt: string; dependencies: string[]; pick: WorkModel; fallback: WorkModel | null; isolate: boolean }
  | { action: 'cancel'; id: string }
  | { action: 'verdict'; sid: string; accepted: boolean }
  | { action: 'native-list'; engine: NativeEngine; cwd: string; cursor?: string }
  | { action: 'native-import'; engine: NativeEngine; cwd: string; native_id: string; title: string }
  | { action: 'groups'; title: string; repos: string[]; contract: string; acceptance: string; pick: WorkModel }
  | { action: 'group-session'; group: string; member?: string }

export interface TaskWorkResponses {
  state: { queue: WorkQueue[]; groups: WorkGroup[] }
  models: WorkRecommendation
  limit: { ok: boolean }
  queue: { id: string }
  cancel: { ok: boolean }
  verdict: { ok: boolean }
  'native-list': { sessions: NativeSession[]; next_cursor: string | null; manual: boolean }
  'native-import': NativeImport
  groups: WorkGroup
  'group-session': { sid: string }
}

export function taskWorkRoute(request: TaskWorkRequest): { method: string; path: string; body?: unknown } | undefined {
  const root = '/agent/taskwork'
  const { action, ...body } = request
  switch (action) {
    case 'state': return { method: 'GET', path: root }
    case 'models': return { method: 'GET', path: `${root}/models?${new URLSearchParams({ cwd: request.cwd })}` }
    case 'native-list': return {
      method: 'GET',
      path: `${root}/native?${new URLSearchParams({ engine: request.engine, cwd: request.cwd, ...(request.cursor ? { cursor: request.cursor } : {}) })}`
    }
    case 'limit': return { method: 'PUT', path: root + '/limit', body }
    case 'queue': return { method: 'POST', path: root + '/queue', body }
    case 'cancel': return /^[a-f0-9]{16}$/.test(request.id)
      ? { method: 'DELETE', path: `${root}/queue/${request.id}` } : undefined
    case 'verdict': return { method: 'POST', path: root + '/verdict', body }
    case 'native-import': return { method: 'POST', path: root + '/native', body }
    case 'groups': return { method: 'POST', path: root + '/groups', body }
    case 'group-session': return { method: 'POST', path: root + '/group-session', body }
  }
}
