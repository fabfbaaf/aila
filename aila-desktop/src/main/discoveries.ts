/** 桌面管家读取发现；界面只能通过已知的主题操作或打开官方来源。 */
import type { Discovery, DiscoveryAction, DiscoverySource, DiscoveryState } from '../shared/discoveries'
import type { AiResult } from '../shared/settings'
import { attempt, json } from './ai'

function call<T>(aiUrl: string, path = '', body?: object, timeout = 5000): Promise<AiResult<T>> {
  return attempt(async () => json<T>(await fetch(`${aiUrl}/discoveries${path}`, {
    method: body ? 'POST' : 'GET',
    ...(body ? { headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) } : {}),
    signal: AbortSignal.timeout(timeout)
  })))
}

export const discoveries = {
  state: (aiUrl: string) => call<DiscoveryState>(aiUrl),
  poll: (aiUrl: string) => call<Discovery | null>(aiUrl, '/poll', {}, 50_000),
  change: (aiUrl: string, source: DiscoverySource, action: DiscoveryAction) =>
    call<DiscoveryState>(aiUrl, '/topics', { source, action }),
  shown: (aiUrl: string, id: string) => call<{ ok: boolean }>(aiUrl, '/shown', { id })
}
