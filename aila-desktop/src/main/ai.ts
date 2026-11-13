// 主进程调用本地 AI 服务的几个接口：设置、资料库、技能包。对话和语音合成在 index.ts 里
import { readFile, stat } from 'fs/promises'
import { basename } from 'path'
import type { KnowledgeAddResult, KnowledgeDoc, SkillInfo, SkillOverrides } from '../shared/cloud'
import type { AiResult, AiSettings, AiSettingsUpdate } from '../shared/settings'

/** 设置窗口用：成功、失败都返回一个结果对象，失败原因能直接显示 */
async function attempt<T>(fn: () => Promise<T>): Promise<AiResult<T>> {
  try {
    return { ok: true, data: await fn() }
  } catch (err) {
    const error = err instanceof TypeError ? '连不上 AI 服务，请先启动 aila-ai' : err instanceof Error ? err.message : String(err)
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

async function json<T>(res: Response): Promise<T> {
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
      const reason = err instanceof TypeError ? '连不上 AI 服务' : err instanceof Error ? err.message : String(err)
      result.errors.push(`${name}：${reason}`)
    }
  }
  return result
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
