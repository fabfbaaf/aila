/** 浏览器调试的编程设置走本机代理，删除失败保持错误结果，不能假装已经删掉。 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { loadAgentRules, removeAgentRule } from './agentRules'

vi.mock('../platform/bridge', () => ({ bridge: null }))
afterEach(() => vi.unstubAllGlobals())

describe('浏览器里的记住的命令', () => {
  it('通过同源 /ai 代理列出和删除，保留命令原文', async () => {
    const rules = [{ project: 'C:\\project', commands: ['npm test && echo "done"'] }]
    const fetchMock = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify(rules)))
      .mockResolvedValueOnce(new Response('{"ok":true}'))
    vi.stubGlobal('fetch', fetchMock)
    expect(await loadAgentRules()).toEqual({ ok: true, data: rules })
    expect((await removeAgentRule(rules[0].project, rules[0].commands[0])).ok).toBe(true)
    expect(fetchMock.mock.calls[0][0]).toBe('/ai/agent/rules')
    expect(fetchMock.mock.calls[1][1].method).toBe('DELETE')
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({ project: rules[0].project, command: rules[0].commands[0] })
  })

  it('404 和服务断连都返回可显示的错误', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(new Response('{"detail":"已经删掉了"}', { status: 404 }))
      .mockRejectedValueOnce(new TypeError('offline')))
    expect(await removeAgentRule('x', 'npm test')).toEqual({ ok: false, error: '已经删掉了' })
    expect(await loadAgentRules()).toEqual({ ok: false, error: '连不上 AI 服务，启动服务后再重试。' })
  })
})
