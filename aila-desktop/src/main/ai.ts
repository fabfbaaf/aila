// 主进程调用本地 AI 服务的几个接口：设置、资料库、技能包、看屏幕的状态。对话和语音合成在 index.ts 里
import { readFile, stat } from 'fs/promises'
import { basename } from 'path'
import type { KnowledgeAddResult, KnowledgeDoc, MemoryEpisode, SkillInfo, SkillOverrides } from '../shared/cloud'
import type {
  AgendaEvent,
  AgentRuleGroup,
  AiResult,
  AiSettings,
  AiSettingsUpdate,
  AndroidStatus,
  CodeProject,
  FixStats,
  PhoneStatus,
  ScheduleInput,
  ScheduleItem,
  ScreenStatus
} from '../shared/settings'
import type { ChunkSource, CodeGraph, LocatedError, OutlineItem } from '../shared/workbench'
import type { ProjectAnswer, ProjectDocuments } from '../shared/evidence'
import { tm } from './lang'

// ---------- 代码星图 ----------

function codeGet<T>(aiUrl: string, path: string, params: Record<string, string | number> = {}): Promise<AiResult<T>> {
  const query = new URLSearchParams(Object.entries(params).map(([k, v]) => [k, String(v)])).toString()
  return attempt(async () =>
    json<T>(await fetch(`${aiUrl}${path}${query ? `?${query}` : ''}`, { signal: AbortSignal.timeout(15000) }))
  )
}

export const codeGraph = (aiUrl: string, id: string): Promise<AiResult<CodeGraph>> =>
  codeGet<CodeGraph>(aiUrl, `/code/projects/${encodeURIComponent(id)}/graph`)

export const codeOutline = (aiUrl: string, id: string, file: string): Promise<AiResult<OutlineItem[]>> =>
  codeGet<OutlineItem[]>(aiUrl, `/code/projects/${encodeURIComponent(id)}/outline`, { file })

export const codeChunk = (aiUrl: string, id: string, file: string, line: number): Promise<AiResult<ChunkSource>> =>
  codeGet<ChunkSource>(aiUrl, `/code/projects/${encodeURIComponent(id)}/chunk`, { file, line })

/** 报错落在哪个登记的项目里；不在任何项目里时 data 是 null */
export function codeLocateAll(aiUrl: string, text: string): Promise<AiResult<LocatedError | null>> {
  return attempt(async () =>
    json<LocatedError | null>(
      await fetch(`${aiUrl}/code/locate`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ text }),
        signal: AbortSignal.timeout(15000)
      })
    )
  )
}

// ---------- 代码库 ----------

export const codeDocuments = (aiUrl: string, id: string): Promise<AiResult<ProjectDocuments>> =>
  codeGet<ProjectDocuments>(aiUrl, `/code/projects/${encodeURIComponent(id)}/documents`)

export function setCodeDocuments(aiUrl: string, id: string, linked: string[]): Promise<AiResult<ProjectDocuments>> {
  return attempt(async () =>
    json<ProjectDocuments>(await fetch(`${aiUrl}/code/projects/${encodeURIComponent(id)}/documents`, {
      method: 'PUT',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ linked }),
      signal: AbortSignal.timeout(10000)
    }))
  )
}

export function codeEvidence(aiUrl: string, id: string, query: string): Promise<AiResult<ProjectAnswer>> {
  return attempt(async () =>
    json<ProjectAnswer>(await fetch(`${aiUrl}/code/projects/${encodeURIComponent(id)}/evidence`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ query }),
      signal: AbortSignal.timeout(90000)
    }))
  )
}

export function listCodeProjects(aiUrl: string): Promise<AiResult<CodeProject[]>> {
  return attempt(async () => json<CodeProject[]>(await fetch(`${aiUrl}/code/projects`, { signal: AbortSignal.timeout(5000) })))
}

/** 修过几次代码、测试通过几次、改进用户代码几次 */
export function getFixStats(aiUrl: string): Promise<AiResult<FixStats>> {
  return attempt(async () => json<FixStats>(await fetch(`${aiUrl}/code/fixes`, { signal: AbortSignal.timeout(3000) })))
}

export function addCodeProject(aiUrl: string, path: string): Promise<AiResult<CodeProject>> {
  return attempt(async () =>
    json<CodeProject>(
      await fetch(`${aiUrl}/code/projects`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ path }),
        signal: AbortSignal.timeout(10000)
      })
    )
  )
}

export function codeProjectAction(aiUrl: string, id: string, action: 'refresh' | 'delete'): Promise<AiResult<unknown>> {
  const url = action === 'refresh' ? `${aiUrl}/code/projects/${encodeURIComponent(id)}/refresh` : `${aiUrl}/code/projects/${encodeURIComponent(id)}`
  return attempt(async () =>
    json<unknown>(await fetch(url, { method: action === 'refresh' ? 'POST' : 'DELETE', signal: AbortSignal.timeout(5000) }))
  )
}

/** 改项目要忽略的文件夹（AI 服务会马上在后台重新扫） */
export function setCodeIgnore(aiUrl: string, id: string, patterns: string[]): Promise<AiResult<CodeProject>> {
  return attempt(async () =>
    json<CodeProject>(
      await fetch(`${aiUrl}/code/projects/${encodeURIComponent(id)}/ignore`, {
        method: 'PUT',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ patterns }),
        signal: AbortSignal.timeout(5000)
      })
    )
  )
}

/** 看屏幕的状态和上次的统计（设置窗口显示） */
export function getScreenStatus(aiUrl: string): Promise<AiResult<ScreenStatus>> {
  return attempt(async () => json<ScreenStatus>(await fetch(`${aiUrl}/screen/status`, { signal: AbortSignal.timeout(3000) })))
}

/** 设置窗口用：成功、失败都返回一个结果对象，失败原因能直接显示 */
export async function attempt<T>(fn: () => Promise<T>): Promise<AiResult<T>> {
  try {
    return { ok: true, data: await fn() }
  } catch (err) {
    const error = err instanceof TypeError ? tm('连不上 AI 服务，请先启动 aila-ai') : err instanceof Error ? err.message : String(err)
    return { ok: false, error }
  }
}

export function getAiSettings(aiUrl: string): Promise<AiResult<AiSettings>> {
  return attempt(async () => json<AiSettings>(await fetch(`${aiUrl}/settings`, { signal: AbortSignal.timeout(5000) })))
}

export function putAiSettings(aiUrl: string, update: AiSettingsUpdate): Promise<AiResult<AiSettings>> {
  return attempt(async () => {
    const res = await fetch(`${aiUrl}/settings`, {
      method: 'PUT',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(update),
      signal: AbortSignal.timeout(10_000)
    })
    return (await json<{ settings: AiSettings }>(res)).settings
  })
}

export function testLlm(aiUrl: string): Promise<AiResult<{ ok: boolean; message: string }>> {
  return attempt(async () =>
    json<{ ok: boolean; message: string }>(await fetch(`${aiUrl}/settings/test`, { method: 'POST', signal: AbortSignal.timeout(30_000) }))
  )
}

const MAX_UPLOAD = 20 * 1024 * 1024

export async function json<T>(res: Response): Promise<T> {
  const data = (await res.json().catch(() => ({}))) as T & { detail?: unknown }
  if (!res.ok) throw new Error(describeDetail(data.detail) ?? `AI 服务返回 ${res.status}`)
  return data
}

/** FastAPI 的错误：字符串，或者参数校验失败时的列表（取第一条的说明） */
function describeDetail(detail: unknown): string | null {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail) && detail[0] && typeof detail[0].msg === 'string') {
    return String(detail[0].msg).replace(/^Value error, /, '')
  }
  return null
}

export async function listKnowledge(aiUrl: string): Promise<KnowledgeDoc[]> {
  return json(await fetch(`${aiUrl}/knowledge`, { signal: AbortSignal.timeout(5000) }))
}

/** 逐个上传文件；一个失败不影响别的，失败原因收集起来给用户看 */
export async function addKnowledge(aiUrl: string, paths: string[]): Promise<KnowledgeAddResult> {
  const result: KnowledgeAddResult = { added: [], errors: [] }
  for (const path of paths) {
    const name = basename(path)
    try {
      if ((await stat(path)).size > MAX_UPLOAD) throw new Error('文件太大了（最多 20MB）')
      const res = await fetch(`${aiUrl}/knowledge?name=${encodeURIComponent(name)}`, {
        method: 'POST',
        headers: { 'content-type': 'application/octet-stream' },
        body: await readFile(path),
        signal: AbortSignal.timeout(5 * 60_000) // 大文件算向量要一会儿
      })
      result.added.push(await json<KnowledgeDoc>(res))
    } catch (err) {
      const reason = err instanceof TypeError ? tm('连不上 AI 服务') : err instanceof Error ? err.message : String(err)
      result.errors.push(`${name}：${reason}`)
    }
  }
  return result
}

/** 记忆库里的经历；AI 服务没开时是空的 */
export async function listEpisodes(aiUrl: string): Promise<MemoryEpisode[]> {
  try {
    return await json<MemoryEpisode[]>(await fetch(`${aiUrl}/memory/episodes?limit=200`, { signal: AbortSignal.timeout(5000) }))
  } catch {
    return []
  }
}

/** 删一条经历（id 给了就删这条，没给就全部删）；返回删没删成 */
export async function forgetEpisodes(aiUrl: string, id?: string): Promise<boolean> {
  try {
    const path = id ? `/memory/episodes/${encodeURIComponent(id)}` : '/memory/episodes'
    const res = await fetch(`${aiUrl}${path}`, { method: 'DELETE', signal: AbortSignal.timeout(5000) })
    return res.ok || res.status === 404
  } catch {
    return false
  }
}

export async function deleteKnowledge(aiUrl: string, id: string): Promise<void> {
  const res = await fetch(`${aiUrl}/knowledge/${encodeURIComponent(id)}`, { method: 'DELETE', signal: AbortSignal.timeout(5000) })
  if (!res.ok && res.status !== 404) throw new Error(`AI 服务返回 ${res.status}`)
}

/** 技能包列表（带上用户的开关）。AI 服务没开时返回 null，菜单里提示一下 */
export async function listSkills(aiUrl: string, overrides: SkillOverrides): Promise<SkillInfo[] | null> {
  try {
    const skills = await json<SkillInfo[]>(await fetch(`${aiUrl}/skills`, { signal: AbortSignal.timeout(800) }))
    return skills.map((s) => ({ ...s, enabled: overrides[s.id] ?? s.enabled }))
  } catch {
    return null
  }
}

/** 设置页：只查看、撤销记住的命令，不提供添加允许规则的入口。 */
// ---------- 手机连接（设置 → 手机） ----------

export function getPhone(aiUrl: string): Promise<AiResult<PhoneStatus>> {
  return attempt(async () => json<PhoneStatus>(await fetch(`${aiUrl}/phone`, { signal: AbortSignal.timeout(5000) })))
}

/** 开关局域网的门，或者告诉它桌面端现在的技能包开关（关掉的手机上也用不了） */
export function setPhone(
  aiUrl: string,
  body: { enabled?: boolean; skills?: Record<string, boolean>; push?: { kind: string; url: string } }
): Promise<AiResult<PhoneStatus>> {
  return attempt(async () => json<PhoneStatus>(await fetch(`${aiUrl}/phone`, {
    method: 'PUT',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(10000)
  })))
}

export function testPhonePush(aiUrl: string): Promise<AiResult<{ ok: boolean }>> {
  return attempt(async () => json(await fetch(`${aiUrl}/phone/push-test`, { method: 'POST', signal: AbortSignal.timeout(15000) })))
}

export function newPhonePairing(aiUrl: string): Promise<AiResult<{ code: string; expires: number; seconds: number }>> {
  return attempt(async () => json(await fetch(`${aiUrl}/phone/pair`, { method: 'POST', signal: AbortSignal.timeout(5000) })))
}

export function forgetPhone(aiUrl: string, id: string): Promise<AiResult<PhoneStatus>> {
  return attempt(async () => json<PhoneStatus>(await fetch(`${aiUrl}/phone/devices/${encodeURIComponent(id)}`, {
    method: 'DELETE',
    signal: AbortSignal.timeout(5000)
  })))
}

// ---------- 日历和邮件（只读） ----------

type AgendaList = { configured: boolean; events: AgendaEvent[]; error: string }

export function getAgendaToday(aiUrl: string): Promise<AiResult<AgendaList>> {
  return attempt(async () => json<AgendaList>(await fetch(`${aiUrl}/agenda/today`, { signal: AbortSignal.timeout(20000) })))
}

/** 接下来几分钟内开始的会（会前提醒） */
export function getAgendaUpcoming(aiUrl: string, minutes = 10): Promise<AiResult<AgendaList>> {
  return attempt(async () => json<AgendaList>(await fetch(`${aiUrl}/agenda/upcoming?minutes=${minutes}`, { signal: AbortSignal.timeout(20000) })))
}

export function testAgenda(aiUrl: string, what: 'calendar' | 'mail'): Promise<AiResult<{ ok: boolean; count: number; unread?: number }>> {
  return attempt(async () => json(await fetch(`${aiUrl}/agenda/test-${what}`, { method: 'POST', signal: AbortSignal.timeout(40000) })))
}

// ---------- 定时任务（设置 → 编程） ----------

async function scheduleCall<T>(aiUrl: string, method: string, path: string, body?: unknown): Promise<AiResult<T>> {
  return attempt(async () => json<T>(await fetch(`${aiUrl}/agent/schedules${path}`, {
    method,
    headers: body === undefined ? undefined : { 'content-type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
    // 「现在跑一次」要先跑测试，可能要好一会儿
    signal: AbortSignal.timeout(path.endsWith('/run') ? 660000 : 10000)
  })))
}

export const listSchedules = (aiUrl: string): Promise<AiResult<{ items: ScheduleItem[]; projects: { name: string; path: string }[] }>> =>
  scheduleCall(aiUrl, 'GET', '')
export const addSchedule = (aiUrl: string, body: ScheduleInput): Promise<AiResult<ScheduleItem>> => scheduleCall(aiUrl, 'POST', '', body)
export const changeSchedule = (aiUrl: string, id: string, body: Partial<ScheduleInput> & { enabled?: boolean }): Promise<AiResult<ScheduleItem>> =>
  scheduleCall(aiUrl, 'PUT', `/${encodeURIComponent(id)}`, body)
export const deleteSchedule = (aiUrl: string, id: string): Promise<AiResult<unknown>> => scheduleCall(aiUrl, 'DELETE', `/${encodeURIComponent(id)}`)
export const runSchedule = (aiUrl: string, id: string): Promise<AiResult<ScheduleItem>> =>
  scheduleCall(aiUrl, 'POST', `/${encodeURIComponent(id)}/run`)

// ---------- 她操作安卓手机（设置 → 手机） ----------

export function getAndroid(aiUrl: string): Promise<AiResult<AndroidStatus>> {
  return attempt(async () => json<AndroidStatus>(await fetch(`${aiUrl}/android`, { signal: AbortSignal.timeout(15000) })))
}

async function sendAndroid<T>(aiUrl: string, method: 'PUT' | 'POST', path: string, body: unknown): Promise<AiResult<T>> {
  return attempt(async () => json<T>(await fetch(`${aiUrl}/android${path}`, {
    method,
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(30000)
  })))
}

export const setAndroid = (aiUrl: string, body: { enabled?: boolean; serial?: string }): Promise<AiResult<AndroidStatus>> =>
  sendAndroid(aiUrl, 'PUT', '', body)
/** 无线调试：先配对（手机上「使用配对码配对」给的地址和码），再连（「无线调试」页面最上面的地址） */
export const pairAndroid = (aiUrl: string, address: string, code: string): Promise<AiResult<AndroidStatus>> =>
  sendAndroid(aiUrl, 'POST', '/pair', { address, code })
export const connectAndroid = (aiUrl: string, address: string): Promise<AiResult<AndroidStatus>> =>
  sendAndroid(aiUrl, 'POST', '/connect', { address })

export function getAgentRules(aiUrl: string): Promise<AiResult<AgentRuleGroup[]>> {
  return attempt(async () => json<AgentRuleGroup[]>(await fetch(`${aiUrl}/agent/rules`, { signal: AbortSignal.timeout(5000) })))
}

export function forgetAgentRule(aiUrl: string, project: string, command: string): Promise<AiResult<unknown>> {
  return attempt(async () => json(await fetch(`${aiUrl}/agent/rules`, {
    method: 'DELETE',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ project, command }),
    signal: AbortSignal.timeout(5000)
  })))
}
