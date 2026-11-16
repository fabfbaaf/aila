import { afterEach, describe, expect, it, vi } from 'vitest'
import type { AgentActivity, AgentEvent, AgentProgress } from '../shared/agent'
import { AgentRelay } from './agent'

/** 假的 AI 服务：会话详情返回项目目录，说话返回一段事件流 */
function fakeService(events: AgentEvent[]): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn(async (url: string) => {
    if (url.endsWith('/messages')) {
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
