import { afterEach, describe, expect, it, vi } from 'vitest'
import type { AgentActivity, AgentEvent, AgentProgress } from '../shared/agent'
import { AgentRelay } from './agent'

/** 假的 AI 服务：会话详情返回项目目录，说话返回一段事件流 */
function fakeService(events: AgentEvent[]): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn(async (url: string) => {
    if (url.endsWith('/messages') || url.endsWith('/plan/approve') || url.endsWith('/plan/continue')) {
      const body = events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join('')
      return new Response(
        new ReadableStream({
          start(c) {
            c.enqueue(new TextEncoder().encode(body))
            c.close()
          }
        }),
        { status: 200 }
      )
    }
    if (url.endsWith('/agent/confirm')) return new Response('{"ok":true}', { status: 200 })
    return new Response(JSON.stringify({ id: 's1', cwd: 'C:\\code\\shop', events: [] }), { status: 200 })
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function relay() {
  const seen = { progress: [] as AgentProgress[], activity: [] as AgentActivity[], confirm: [] as unknown[], ended: 0 }
  const r = new AgentRelay('http://ai', {
    emit: () => {},
    ended: () => seen.ended++,
    progress: (p) => seen.progress.push(p),
    activity: (a) => seen.activity.push(a),
    confirm: (c) => seen.confirm.push(c)
  })
  return { r, seen }
}

async function until(cond: () => boolean): Promise<void> {
  for (let i = 0; i < 100 && !cond(); i++) await new Promise((res) => setTimeout(res, 5))
}

const TURN: AgentEvent[] = [
  { type: 'user', seq: 1, text: '改 README' },
  {
    type: 'assistant',
    seq: 2,
    text: '',
    tool_calls: [
      { id: 'a', name: 'read_file', arguments: '{"path":"README.md"}' },
      { id: 'b', name: 'edit_file', arguments: '{"path":"README.md","old_string":"x","new_string":"y"}' }
    ]
  },
  { type: 'tool_start', id: 'a', name: 'read_file', status: '读 README.md' },
  { type: 'tool', seq: 3, id: 'a', name: 'read_file', status: 'ok', content: '     1\tx' },
  { type: 'confirm', seq: 4, id: 'c1', call_id: 'b', tool: 'edit_file', kind: 'edit', action: '改 README.md', detail: '-x\n+y' },
  { type: 'confirm_end', seq: 5, id: 'c1', result: 'approved' },
  { type: 'tool_start', id: 'b', name: 'edit_file', status: '改 README.md' },
  { type: 'tool', seq: 6, id: 'b', name: 'edit_file', status: 'ok', content: '改好了', file: 'README.md' },
  { type: 'turn_end', seq: 7, reason: 'done' }
]

afterEach(() => vi.unstubAllGlobals())

describe('工作窗口的转发（主进程）', () => {
  it('新建会话传零预算，修改上限可清空且会话路径编码', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ type: 'budget', amount: null })))
    vi.stubGlobal('fetch', fetchMock)
    const { r } = relay()
    await r.create('C:/shop', 'ask', false, 0)
    expect(fetchMock).toHaveBeenLastCalledWith('http://ai/agent/sessions', expect.objectContaining({
      method: 'POST', body: JSON.stringify({ cwd: 'C:/shop', mode: 'ask', isolate: false, budget: 0 })
    }))
    await r.setBudget('s /1', null)
    expect(fetchMock).toHaveBeenLastCalledWith('http://ai/agent/sessions/s%20%2F1/budget', expect.objectContaining({
      method: 'PUT', body: JSON.stringify({ amount: null })
    }))
  })

  it('预算停止通过独立进展通知桌面角色', async () => {
    fakeService([{ type: 'user', seq: 1, text: '做任务' }, { type: 'turn_end', seq: 2, reason: 'budget', cost: 0.004 }])
    const { r, seen } = relay()
    r.send('s1', '做任务')
    await until(() => seen.ended > 0)
    expect(seen.progress).toEqual([{ kind: 'start', title: '做任务' }, { kind: 'budget' }])
  })

  it('撤销请求锁定预览的轮次，服务拒绝时原样显示原因', async () => {
    const event = { type: 'undo', seq: 12, t: 1, turn_seq: 4, restored: ['a.py'], skipped: {}, commands: false }
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(event)))
      .mockResolvedValueOnce(new Response('{"detail":"最近一轮已经变了"}', { status: 409 }))
    vi.stubGlobal('fetch', fetchMock)
    const { r } = relay()
    expect(await r.undo('s /1', 4)).toEqual({ ok: true, data: event })
    expect(fetchMock).toHaveBeenCalledWith('http://ai/agent/sessions/s%20%2F1/undo', expect.objectContaining({
      method: 'POST', body: JSON.stringify({ turn_seq: 4 })
    }))
    expect(await r.undo('s /1', 4)).toEqual({ ok: false, error: '最近一轮已经变了' })
  })

  it('改名转发 PATCH，标题放在请求体，会话 id 按路径编码', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ id: 's /1', cwd: 'C:/code', title: '新标题' })))
    vi.stubGlobal('fetch', fetchMock)
    const { r } = relay()
    expect((await r.rename('s /1', '新标题')).data?.title).toBe('新标题')
    expect(fetchMock).toHaveBeenCalledWith('http://ai/agent/sessions/s%20%2F1', expect.objectContaining({
      method: 'PATCH', body: JSON.stringify({ title: '新标题' })
    }))
  })

  it('搜索把关键词放进地址，审查改动带 review 标记，进展里用一句话的标题', async () => {
    const fetchMock = fakeService([{ type: 'user', seq: 1, text: '很长的审查请求', review: true, title: '审查没提交的改动' }])
    const { r, seen } = relay()
    fetchMock.mockImplementationOnce(async () => new Response('[]')).mockImplementationOnce(async () => new Response('[]'))
    await r.list('  购物车 ')
    expect(fetchMock).toHaveBeenLastCalledWith(`http://ai/agent/sessions?q=${encodeURIComponent('购物车')}`, expect.anything())
    await r.list()
    expect(fetchMock).toHaveBeenLastCalledWith('http://ai/agent/sessions', expect.anything())
    r.send('s1', '', true)
    await vi.waitFor(() => expect(seen.progress.length).toBe(1))
    expect(fetchMock).toHaveBeenLastCalledWith('http://ai/agent/sessions/s1/messages', expect.objectContaining({
      method: 'POST', body: JSON.stringify({ text: '', review: true })
    }))
    expect(seen.progress[0]).toMatchObject({ kind: 'start', title: '审查没提交的改动' })
    r.send('s1', '改 README')
    expect(fetchMock).toHaveBeenLastCalledWith('http://ai/agent/sessions/s1/messages', expect.objectContaining({
      body: JSON.stringify({ text: '改 README' })
    }))
  })

  it('项目记忆按项目目录看、加、删，目录放在查询参数或请求体里', async () => {
    const fetchMock = vi.fn(async () => new Response('[]'))
    vi.stubGlobal('fetch', fetchMock)
    const { r } = relay()
    await r.memory('C:\\code\\shop & co')
    expect(fetchMock).toHaveBeenLastCalledWith(
      'http://ai/agent/memory?cwd=C%3A%5Ccode%5Cshop%20%26%20co',
      expect.objectContaining({ method: 'GET' })
    )
    await r.addMemory('C:/shop', '改完跑 npm test')
    expect(fetchMock).toHaveBeenLastCalledWith('http://ai/agent/memory', expect.objectContaining({
      method: 'POST', body: JSON.stringify({ cwd: 'C:/shop', text: '改完跑 npm test' })
    }))
    await r.forgetMemory('C:/shop', 'ab12')
    expect(fetchMock).toHaveBeenLastCalledWith('http://ai/agent/memory', expect.objectContaining({
      method: 'DELETE', body: JSON.stringify({ cwd: 'C:/shop', id: 'ab12' })
    }))
  })

  it('MCP 服务的管理请求：服务名按路径编码，配置原样放在请求体里', async () => {
    const fetchMock = vi.fn(async () => new Response('[]'))
    vi.stubGlobal('fetch', fetchMock)
    const { r } = relay()
    await r.mcp()
    expect(fetchMock).toHaveBeenLastCalledWith('http://ai/agent/mcp', expect.objectContaining({ method: 'GET' }))
    await r.mcpPreview('{"mcpServers":{}}')
    expect(fetchMock).toHaveBeenLastCalledWith('http://ai/agent/mcp/preview', expect.objectContaining({
      method: 'POST', body: JSON.stringify({ json: '{"mcpServers":{}}' })
    }))
    await r.mcpImport('{"a":{"command":"npx"}}')
    expect(fetchMock).toHaveBeenLastCalledWith('http://ai/agent/mcp/import', expect.objectContaining({ method: 'POST' }))
    await r.mcpRestart('my-db')
    expect(fetchMock).toHaveBeenLastCalledWith('http://ai/agent/mcp/my-db/restart', expect.objectContaining({ method: 'POST' }))
    await r.mcpDisable('my-db', true)
    expect(fetchMock).toHaveBeenLastCalledWith('http://ai/agent/mcp/my-db/disabled', expect.objectContaining({
      method: 'PUT', body: JSON.stringify({ disabled: true })
    }))
    await r.mcpForget('my-db', 'query')
    expect(fetchMock).toHaveBeenLastCalledWith('http://ai/agent/mcp/my-db/allow', expect.objectContaining({
      method: 'DELETE', body: JSON.stringify({ tool: 'query' })
    }))
    await r.mcpRemove('my-db')
    expect(fetchMock).toHaveBeenLastCalledWith('http://ai/agent/mcp/my-db', expect.objectContaining({ method: 'DELETE' }))
  })

  it('长任务：出方案、批准、接着做、不要；按阶段做时每轮不说做完了，阶段和计划做完才说', async () => {
    const fetchMock = fakeService([
      { type: 'user', seq: 1, text: '开始第 1/2 阶段……', plan_stage: 0, title: '第 1/2 阶段：写函数' },
      { type: 'tool', seq: 2, id: 'c1', name: 'stage_done', status: 'ok', stage: 0 },
      { type: 'turn_end', seq: 3, reason: 'done' },
      { type: 'plan_pause', seq: 4, reason: 'pause_each', text: '等你看一眼', stage: 1 }
    ])
    const { r, seen } = relay()
    r.send('s1', '加打折', false, true)
    expect(fetchMock).toHaveBeenLastCalledWith('http://ai/agent/sessions/s1/messages', expect.objectContaining({
      body: JSON.stringify({ text: '加打折', plan: true })
    }))
    await vi.waitFor(() => expect(seen.progress.length).toBe(4))
    r.planApprove('s1', true)
    expect(fetchMock).toHaveBeenLastCalledWith('http://ai/agent/sessions/s1/plan/approve', expect.objectContaining({
      method: 'POST', body: JSON.stringify({ pause_each: true })
    }))
    await vi.waitFor(() => expect(seen.progress.length).toBe(8))
    expect(seen.progress.slice(4)).toEqual([
      { kind: 'start', title: '第 1/2 阶段：写函数' },
      { kind: 'stage', title: '写函数' },
      { kind: 'stopped', reason: 'plan' }, // 这一轮做完不说「做完了」
      { kind: 'plan_pause', text: '等你看一眼' }
    ])
    r.planContinue('s1')
    expect(fetchMock).toHaveBeenLastCalledWith('http://ai/agent/sessions/s1/plan/continue', expect.objectContaining({ method: 'POST' }))
    await r.planReject('s1')
    expect(fetchMock).toHaveBeenLastCalledWith('http://ai/agent/sessions/s1/plan/reject', expect.objectContaining({ method: 'POST' }))
  })

  it('删不了时保留等确认的状态，删除后清理它', async () => {
    const fetchMock = fakeService(TURN.slice(0, 5))
    const { r, seen } = relay()
    r.send('s1', '改 README')
    await until(() => seen.ended > 0)
    fetchMock.mockImplementationOnce(async () => new Response('{"detail":"先打断"}', { status: 409 }))
    expect(await r.delete('s1')).toEqual({ ok: false, error: '先打断' })
    expect(await r.approvePending('c1')).toBe(true)
    fetchMock.mockImplementationOnce(async () => new Response('{"ok":true}'))
    expect((await r.delete('s1')).ok).toBe(true)
    expect(fetchMock).toHaveBeenLastCalledWith('http://ai/agent/sessions/s1', expect.objectContaining({ method: 'DELETE' }))
    expect(await r.approvePending('c1')).toBe(false)
    expect(seen.confirm.at(-1)).toBe(null)
  })

  it('进展给她、在哪个文件上干活给星图、等确认的改文件给星图', async () => {
    fakeService(TURN)
    const { r, seen } = relay()
    await r.get('s1') // 记下项目目录
    r.send('s1', '改 README')
    await until(() => seen.ended > 0)

    expect(seen.progress.map((p) => p.kind)).toEqual(['start', 'confirm', 'done'])
    expect(seen.progress[2]).toEqual({ kind: 'done', files: 1 })
    expect(seen.activity).toEqual([
      { sid: 's1', cwd: 'C:\\code\\shop', file: 'README.md', kind: 'read' },
      { sid: 's1', cwd: 'C:\\code\\shop', file: 'README.md', kind: 'edit' }
    ])
    expect(seen.confirm).toEqual([{ sid: 's1', id: 'c1', detail: '-x\n+y' }, null]) // 回答以后星图上收起
  })

  it('星图里双手合十只能确认正在等的那个', async () => {
    const fetchMock = fakeService(TURN.slice(0, 5)) // 停在等确认
    const { r, seen } = relay()
    r.send('s1', '改 README')
    await until(() => seen.ended > 0)
    expect(await r.approvePending('别的')).toBe(false)
    expect(await r.approvePending('c1')).toBe(true)
    const call = fetchMock.mock.calls.find(([url]) => String(url).endsWith('/agent/confirm'))
    expect(JSON.parse(String((call?.[1] as RequestInit).body))).toEqual({ id: 'c1', approved: true, remember: false })
  })
})
