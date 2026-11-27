// 「看懂项目」的数据从哪来：桌面版经主进程转发到 AI 服务；浏览器里调试时经 Vite 代理（/ai）直接请求
import type { AiResult, CodeProject } from '../../../shared/settings'
import type {
  AreaView, ChainView, Diagnosis, EndpointInfo, ExplainDepth, ExplainTarget, Explanation, FileDetail, FunctionDetail
} from '../../../shared/understand'
import { bridge } from '../platform/bridge'
import { t } from '../i18n'

export interface UnderstandSource {
  projects(): Promise<CodeProject[]>
  area(project: string, path: string, tests?: boolean): Promise<AreaView>
  chain(project: string, file: string, endpoint?: string): Promise<ChainView>
  endpoints(project: string): Promise<EndpointInfo[]>
  file(project: string, file: string): Promise<FileDetail>
  fn(project: string, file: string, line: number): Promise<FunctionDetail>
  explain(project: string, target: ExplainTarget): Promise<Explanation>
  diagnose(project: string, text: string, depth: ExplainDepth): Promise<Diagnosis>
  /** 让桌面上的她念出来（浏览器调试时只打印） */
  say(text: string): void
  /** 去 设置 → 代码库 登记项目 */
  openSettings(): void
}

function unwrap<T>(r: AiResult<T>): T {
  if (!r.ok) throw new Error(r.error ?? t('请求失败'))
  return r.data as T
}

function desktop(api: NonNullable<typeof bridge>): UnderstandSource {
  return {
    projects: async () => unwrap(await api.codeList()),
    area: async (p, path, tests = false) => unwrap(await api.codeArea(p, path, tests)),
    chain: async (p, file, endpoint = '') => unwrap(await api.codeChain(p, file, endpoint)),
    endpoints: async (p) => unwrap(await api.codeEndpoints(p)),
    file: async (p, f) => unwrap(await api.codeFile(p, f)),
    fn: async (p, f, line) => unwrap(await api.codeFunction(p, f, line)),
    explain: async (p, target) => unwrap(await api.codeExplain(p, target)),
    diagnose: async (p, text, depth) => unwrap(await api.codeDiagnose(p, text, depth)),
    say: (text) => api.sayToAila(text),
    openSettings: () => api.openSettings('code')
  }
}

function web(): UnderstandSource {
  const enc = encodeURIComponent
  const ask = async <T>(path: string, body?: unknown): Promise<T> => {
    const res = await fetch(`/ai${path}`, body === undefined ? undefined : {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body)
    })
    if (!res.ok) {
      const detail = await res.json().catch(() => null) as { detail?: unknown } | null
      throw new Error(typeof detail?.detail === 'string' ? detail.detail : t('AI 服务返回 {status}', { status: res.status }))
    }
    return (await res.json()) as T
  }
  const p = (id: string, tail: string): string => `/code/projects/${enc(id)}/${tail}`
  return {
    projects: () => ask('/code/projects'),
    area: (id, path, tests = false) => ask(`${p(id, 'area')}?path=${enc(path)}&tests=${tests}`),
    chain: (id, file, endpoint = '') => ask(`${p(id, 'chain')}?file=${enc(file)}&endpoint=${enc(endpoint)}`),
    endpoints: (id) => ask(p(id, 'endpoints')),
    file: (id, f) => ask(`${p(id, 'file')}?file=${enc(f)}`),
    fn: (id, f, line) => ask(`${p(id, 'function')}?file=${enc(f)}&line=${line}`),
    explain: (id, target) => ask(p(id, 'explain'), target),
    diagnose: (id, text, depth) => ask(p(id, 'diagnose'), { text, depth }),
    say: (text) => console.info('[看懂项目] 想让她念：', text),
    openSettings: () => console.info('[看懂项目] 打开设置 → 代码库')
  }
}

export function createUnderstandSource(): UnderstandSource {
  return bridge ? desktop(bridge) : web()
}
