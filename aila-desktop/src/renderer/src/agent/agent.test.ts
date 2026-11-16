import { describe, expect, it } from 'vitest'
import { reactive } from 'vue'
import type { AgentEvent } from '../../../shared/agent'
import { Transcript, endStats, endText, type EndItem, type MergeItem, type ToolItem } from './fold'
import { renderMarkdown } from './markdown'

const ev = (type: string, extra: Record<string, unknown> = {}): AgentEvent => ({ type, ...extra })

function turn(): AgentEvent[] {
  return [
    ev('start', { seq: 0, mode: 'ask', model: 'deepseek-chat' }),
    ev('user', { seq: 1, t: 100, text: '加运费' }),
    ev('text_delta', { text: '我先' }),
    ev('text_delta', { text: '看看。' }),
    ev('assistant', { seq: 2, text: '我先看看。', tool_calls: [{ id: 'a', name: 'edit_file', arguments: '{"path":"cart.py"}' }] }),
    ev('usage', { seq: 3, prompt: 1000, completion: 50, cache_hit: 900, cache_miss: 100 }),
    ev('confirm', { seq: 4, id: 'c1', call_id: 'a', tool: 'edit_file', kind: 'edit', action: '改 cart.py', detail: '@@ -1 +1 @@\n-x\n+y', can_remember: true }),
    ev('confirm_end', { seq: 5, id: 'c1', result: 'approved', remember: true }),
    ev('tool_start', { id: 'a', name: 'edit_file', status: '改 cart.py' }),
    ev('tool', { seq: 6, id: 'a', name: 'edit_file', status: 'ok', content: '改好了 cart.py（+1 −1）', diff: '-x\n+y', file: 'cart.py' }),
    ev('assistant', { seq: 7, text: '改好了。', tool_calls: [] }),
    ev('usage', { seq: 8, prompt: 1100, completion: 10, cache_hit: 1000, cache_miss: 100 }),
    ev('turn_end', { seq: 9, t: 103.5, reason: 'done' })
  ]
}

describe('工作窗口：事件折叠', () => {
  it('重放后台命令和任务工具时显示对应的操作标题', () => {
    const t = new Transcript()
    t.apply(ev('assistant', { seq: 1, text: '', tool_calls: [
      { id: 'a', name: 'run_command', arguments: '{"command":"npm test","background":false}' },
      { id: 'b', name: 'run_command', arguments: '{"command":"npm run dev","description":"启动开发服务器","background":true}' },
      { id: 'c', name: 'job_output', arguments: '{"id":"job-1"}' },
      { id: 'd', name: 'job_stop', arguments: '{"id":"job-1"}' }
    ] }))
    for (const [id, name] of [['a', 'run_command'], ['b', 'run_command'], ['c', 'job_output'], ['d', 'job_stop']]) {
      t.apply(ev('tool', { id, name, status: 'ok', content: 'job-1' }))
    }
    expect(t.items.filter((i) => i.kind === 'tool').map((i) => (i as ToolItem).action)).toEqual([
      'npm test', '后台：启动开发服务器', '看后台任务 job-1 的输出', '停掉后台任务 job-1'
    ])
  })

  it('一轮：文字、带确认的工具卡片、统计', () => {
    const t = new Transcript()
    for (const e of turn()) t.apply(e)
    expect(t.items.map((i) => i.kind)).toEqual(['user', 'text', 'tool', 'text', 'end'])
    const tool = t.items[2] as ToolItem
    expect(tool).toMatchObject({ name: 'edit_file', action: '改 cart.py', status: 'ok', file: 'cart.py' })
    expect(tool.confirm).toMatchObject({ result: 'approved', remember: true, canRemember: true })
    const end = t.items[4] as EndItem
    expect(endText(end)).toBe('做完了')
    expect(endStats(end)).toBe('2 步 · 3.5 秒 · 输入 2.1k（命中缓存 90%） · 输出 60')
    expect([...t.files]).toEqual([['cart.py', 1]])
    expect(t.running).toBe(false)
    expect(t.model).toBe('deepseek-chat')
  })

  it('补发和实时重叠时不重复', () => {
    const t = new Transcript()
    const events = turn()
    for (const e of events) t.apply(e)
    for (const e of events.filter((x) => x.seq !== undefined)) t.apply(e)
    expect(t.items).toHaveLength(5)
  })

  it('等确认时能找到那张卡片；被打断后卡片不再转圈', () => {
    const t = new Transcript()
    for (const e of turn().slice(0, 7)) t.apply(e)
    expect(t.pending?.confirm?.id).toBe('c1')
    expect(t.running).toBe(true)
    t.apply(ev('turn_end', { seq: 20, reason: 'interrupted' }))
    expect(t.pending).toBeUndefined()
    expect((t.items.find((i) => i.kind === 'tool') as ToolItem).status).toBe('interrupted')
  })

  it('不同轮里的工具调用 id 一样（模型每轮从头编号）时不会改到上一轮的卡片', () => {
    const t = new Transcript()
    const round = (n: number, tool: string): AgentEvent[] => [
      ev('user', { seq: n * 10, text: `第 ${n} 轮` }),
      ev('assistant', { seq: n * 10 + 1, text: '', tool_calls: [{ id: 'call_0', name: tool, arguments: '{}' }] }),
      ev('tool', { seq: n * 10 + 2, id: 'call_0', name: tool, status: 'ok', content: tool }),
      ev('turn_end', { seq: n * 10 + 3, reason: 'done' })
    ]
    for (const e of [...round(1, 'list_files'), ...round(2, 'todo_write')]) t.apply(e)
    const tools = t.items.filter((i) => i.kind === 'tool') as ToolItem[]
    expect(tools.map((x) => x.name)).toEqual(['list_files', 'todo_write'])
    expect(new Set(t.items.map((i) => i.key)).size).toBe(t.items.length)
  })

  it('在新分支上做：合并结果、放弃分支', () => {
    const t = new Transcript()
    t.apply(ev('start', { seq: 0, mode: 'ask', branch: { branch: 'aila/task-abc', worktree: 'x' } }))
    expect(t.branch).toBe('aila/task-abc')
    t.apply(ev('merge', { seq: 1, merged: false, reason: '没找到这个项目的测试命令，没法自动验证。', files: ['a.py'], tests: [], conflicts: [] }))
    t.apply(ev('merge', { seq: 2, merged: true, reason: '测试通过……', files: ['a.py'], tests: [{ label: 'pytest', ok: true, seconds: 1.2, tail: '' }], conflicts: [] }))
    const merges = t.items.filter((i) => i.kind === 'merge') as MergeItem[]
    expect(merges.map((m) => [m.merged, m.noTests])).toEqual([[false, true], [true, false]])
    t.apply(ev('branch_closed', { seq: 3, branch: 'aila/task-abc' }))
    expect(t.branch).toBeNull()
    expect(t.closed).toBe(true)
  })

  it('在响应式对象里用（界面就是这么用的）', () => {
    const t = reactive(new Transcript())
    for (const e of turn()) t.apply(e)
    expect((t.items[2] as ToolItem).status).toBe('ok')
  })

  it('待办清单和模式切换', () => {
    const t = new Transcript()
    t.apply(ev('start', { seq: 0, mode: 'ask' }))
    t.apply(ev('tool', { seq: 1, id: 'x', name: 'todo_write', status: 'ok', content: '', todos: [{ content: '读代码', status: 'completed' }] }))
    t.apply(ev('mode', { seq: 2, mode: 'auto' }))
    expect(t.todos[0].content).toBe('读代码')
    expect(t.mode).toBe('auto')
    expect(t.items[t.items.length - 1]).toMatchObject({ kind: 'note', text: '权限模式：工作区内自动' })
  })
})

describe('工作窗口：Markdown', () => {
  it('常用的格式', () => {
    const html = renderMarkdown('## 结果\n\n改了 **两处**，见 `cart.py:12`：\n\n- 加运费\n- 改测试\n\n```py\nx = 1\n```')
    expect(html).toBe(
      '<h4>结果</h4><p>改了 <strong>两处</strong>，见 <code>cart.py:12</code>：</p><ul><li>加运费</li><li>改测试</li></ul>' +
        '<pre data-lang="py"><code>x = 1</code></pre>'
    )
  })

  it('HTML 一律原样显示，不会被执行', () => {
    const html = renderMarkdown('<img src=x onerror=alert(1)> [点我](javascript:alert(1)) `<b>`')
    expect(html).not.toContain('<img')
    expect(html).not.toContain('<b>')
    expect(html).toContain('&lt;img')
    expect(html).toContain('<span class="md-link"') // 链接只显示，不能点
    expect(html).not.toContain('href')
  })

  it('代码块里的符号不处理', () => {
    expect(renderMarkdown('```\n**不是粗体** <div>\n```')).toBe('<pre><code>**不是粗体** &lt;div&gt;</code></pre>')
  })
})
