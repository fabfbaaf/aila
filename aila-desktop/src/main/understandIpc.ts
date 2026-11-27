/**
 * 「看懂项目」的 IPC：只认主窗口（让 AI 讲解要花钱），参数先在这里挡一下（类型、长度），合不合法 AI 服务再查。
 * 文件只能是索引里有的（AI 服务那边查），这里不读文件。
 */
import type { IpcMain, WebContents } from 'electron'
import type { AiResult } from '../shared/settings'
import type { ExplainDepth, ExplainLevel, ExplainTarget } from '../shared/understand'
import { tm } from './lang'

export interface UnderstandRelay {
  area(id: string, path: string, tests: boolean): Promise<AiResult<unknown>>
  chain(id: string, file: string, endpoint: string): Promise<AiResult<unknown>>
  endpoints(id: string): Promise<AiResult<unknown>>
  file(id: string, file: string): Promise<AiResult<unknown>>
  fn(id: string, file: string, line: number): Promise<AiResult<unknown>>
  explain(id: string, target: ExplainTarget): Promise<AiResult<unknown>>
  diagnose(id: string, text: string, depth: ExplainDepth): Promise<AiResult<unknown>>
}

const text = (v: unknown, max: number, allowEmpty = true): v is string =>
  typeof v === 'string' && v.length <= max && (allowEmpty || v.length > 0)
const LEVELS: ExplainLevel[] = ['area', 'chain', 'file', 'function']
const depthOf = (v: unknown): ExplainDepth => (v === 'dev' ? 'dev' : 'beginner')

/** 页面传来的讲解目标：只留认识的字段，类型不对就是 null */
export function cleanTarget(raw: unknown): ExplainTarget | null {
  if (!raw || typeof raw !== 'object') return null
  const r = raw as Record<string, unknown>
  if (!LEVELS.includes(r['level'] as ExplainLevel)) return null
  const path = r['path'] ?? ''
  const file = r['file'] ?? ''
  const endpoint = r['endpoint'] ?? ''
  const line = r['line'] ?? 0
  if (!text(path, 1000) || !text(file, 1000) || !text(endpoint, 300)) return null
  if (typeof line !== 'number' || !Number.isInteger(line) || line < 0) return null
  return { level: r['level'] as ExplainLevel, path, file, endpoint, line, depth: depthOf(r['depth']) }
}

export function registerUnderstandIpc(
  ipc: Pick<IpcMain, 'handle'>,
  relay: UnderstandRelay,
  fromMain: (event: { sender: WebContents }) => boolean
): void {
  const notAllowed = { ok: false, error: tm('只有主窗口能用') }
  const bad = { ok: false, error: tm('参数不对。') }
  const id = (v: unknown): v is string => text(v, 100, false)
  ipc.handle('code:area', (e, pid: unknown, path: unknown, tests: unknown) =>
    !fromMain(e) ? notAllowed : id(pid) && text(path, 1000) ? relay.area(pid, path, tests === true) : bad)
  ipc.handle('code:chain', (e, pid: unknown, file: unknown, endpoint: unknown) =>
    !fromMain(e) ? notAllowed : id(pid) && text(file, 1000) && text(endpoint, 300) && (file || endpoint) ? relay.chain(pid, file, endpoint) : bad)
  ipc.handle('code:endpoints', (e, pid: unknown) => (!fromMain(e) ? notAllowed : id(pid) ? relay.endpoints(pid) : bad))
  ipc.handle('code:file', (e, pid: unknown, file: unknown) =>
    !fromMain(e) ? notAllowed : id(pid) && text(file, 1000, false) ? relay.file(pid, file) : bad)
  ipc.handle('code:function', (e, pid: unknown, file: unknown, line: unknown) =>
    !fromMain(e) ? notAllowed
      : id(pid) && text(file, 1000, false) && typeof line === 'number' && Number.isInteger(line) && line > 0 ? relay.fn(pid, file, line) : bad)
  ipc.handle('code:explain', (e, pid: unknown, target: unknown) => {
    if (!fromMain(e)) return notAllowed
    const clean = cleanTarget(target)
    return id(pid) && clean ? relay.explain(pid, clean) : bad
  })
  ipc.handle('code:diagnose', (e, pid: unknown, errorText: unknown, depth: unknown) =>
    !fromMain(e) ? notAllowed : id(pid) && text(errorText, 20000, false) ? relay.diagnose(pid, errorText, depthOf(depth)) : bad)
}
