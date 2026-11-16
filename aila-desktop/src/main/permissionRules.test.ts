/** 编程设置的主进程请求：删除按完整原文发送，服务错误要能显示给用户。 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { forgetAgentRule, getAgentRules } from './ai'

afterEach(() => vi.unstubAllGlobals())

describe('记住的命令请求', () => {
  it('列出项目并按完整路径、命令发送删除请求', async () => {
    const rules = [{ project: 'C:\\项目 a', commands: ['npm test -- --grep "a b"'] }]
    const fetchMock = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify(rules)))
      .mockResolvedValueOnce(new Response('{"ok":true}'))
    vi.stubGlobal('fetch', fetchMock)
    expect(await getAgentRules('http://ai')).toEqual({ ok: true, data: rules })
    expect((await forgetAgentRule('http://ai', rules[0].project, rules[0].commands[0])).ok).toBe(true)
    const [url, init] = fetchMock.mock.calls[1]
    expect(url).toBe('http://ai/agent/rules')
    expect(init.method).toBe('DELETE')
    expect(JSON.parse(init.body)).toEqual({ project: rules[0].project, command: rules[0].commands[0] })
  })

  it('删除不存在的规则时保留服务的错误说明，断连时返回错误结果', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(new Response('{"detail":"没有这条记住的命令"}', { status: 404 }))
      .mockRejectedValueOnce(new TypeError('offline')))
    expect(await forgetAgentRule('http://ai', 'x', 'npm test')).toEqual({ ok: false, error: '没有这条记住的命令' })
    expect((await getAgentRules('http://ai')).ok).toBe(false)
  })
})
