import { describe, expect, it } from 'vitest'
import { computed, reactive } from 'vue'
import type { AgentEvent } from '../../../shared/agent'
import { Transcript, endStats, endText, type EndItem, type MergeItem, type ToolItem } from './fold'
import { renderMarkdown } from './markdown'
import { buildTrace } from './trace'

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
  it('长任务：出方案、阶段开始只显示标题；决定、停下、做完是一行说明', () => {
    const t = new Transcript()
    t.apply(ev('start', { seq: 0, mode: 'ask' }))
    t.apply(ev('user', { seq: 1, t: 1, text: '先为下面这个目标出一份分阶段的计划……', plan_request: true, title: '出方案：加打折' }))
    t.apply(ev('plan_decision', { seq: 2, approved: true, pause_each: false }))
    t.apply(ev('user', { seq: 3, t: 2, text: '开始第 1/2 阶段……', plan_stage: 0, title: '第 1/2 阶段：写函数' }))
    t.apply(ev('plan_pause', { seq: 4, reason: 'blocked', text: '要你定折扣', stage: 0 }))
    t.apply(ev('plan_end', { seq: 5 }))
    const rows = t.items.map((i) => (i.kind === 'user' ? `${i.tag}:${i.text}` : i.kind === 'note' ? i.text : i.kind))
    expect(rows).toEqual([
      'plan:出方案：加打折',
      '你批准了计划：按阶段做下去',
      'stage:第 1/2 阶段：写函数',
      '计划停在第 1 阶段：要你定折扣',
      '计划全部做完了'
    ])
  })

  it('/review 那一轮只显示一句标题，不把整段请求摆出来', () => {
    const t = new Transcript()
    t.apply(ev('start', { seq: 0, mode: 'ask' }))
    t.apply(ev('user', { seq: 1, t: 1, text: '请审查……<改动清单>很长</改动清单>', review: true, title: '审查没提交的改动：登录' }))
    t.apply(ev('user', { seq: 2, t: 2, text: '普通的一句话' }))
    expect(t.items.map((i) => (i.kind === 'user' ? [i.text, !!i.review] : i.kind))).toEqual([
      ['审查没提交的改动：登录', true],
      ['普通的一句话', false]
    ])
  })

  it('回放预算变更和每轮费用，未知价格和旧事件不伪装成零费用', () => {
    const t = new Transcript()
    t.apply(ev('start', { seq: 0, budget: 0 }))
    expect(t.budget).toBe(0)
    t.apply(ev('user', { seq: 1, t: 1 }))
    t.apply(ev('turn_end', { seq: 2, t: 2, reason: 'budget', cost: 0.004 }))
    const end = t.items.find((item) => item.kind === 'end') as EndItem
    expect(endStats(end)).toBe('0 步 · 1.0 秒 · ¥0.004')
    expect(endText(end)).toContain('调高或取消上限后说「继续」')
    expect(endStats({ ...end, cost: null })).not.toContain('¥')
    expect(endStats({ ...end, cost: undefined })).not.toContain('¥')
    expect(endStats({ ...end, cost: 0 })).toContain('¥0.00')
    t.apply(ev('budget', { seq: 3, amount: 0.5 }))
    expect(t.budget).toBe(0.5)
    t.apply(ev('budget', { seq: 4, amount: null }))
    expect(t.budget).toBeNull()
    t.apply(ev('budget', { seq: 5, amount: -1 }))
    expect(t.budget).toBeNull()
  })

  it('只给最近一轮撤销入口，预览去重并区分新建、修改、未留底和命令', () => {
    const t = reactive(new Transcript())
    const target = computed(() => t.undoTarget)
    t.apply(ev('user', { seq: 1, t: 10 }))
    t.apply(ev('tool', { seq: 2, id: 'a', name: 'write_file', status: 'ok', file: '新建.txt', created: true, after: 'a' }))
    t.apply(ev('tool', { seq: 3, id: 'b', name: 'edit_file', status: 'ok', file: '新建.txt', created: false, snap: 0, after: 'b' }))
    t.apply(ev('tool', { seq: 4, id: 'c', name: 'edit_file', status: 'ok', file: '修改.txt', created: false, snap: 1, after: 'c' }))
    t.apply(ev('tool', { seq: 5, id: 'd', name: 'edit_file', status: 'ok', file: '旧记录.txt' }))
    t.apply(ev('tool', { seq: 6, id: 'e', name: 'edit_file', status: 'error', file: '没写到.txt', created: false, snap: 2, after: 'd' }))
    t.apply(ev('tool', { seq: 7, id: 'f', name: 'run_command', status: 'ok', exit_code: 0 }))
    expect(target.value).toBeUndefined()
    t.apply(ev('turn_end', { seq: 8, t: 12, reason: 'interrupted' }))
    expect(target.value?.undo).toEqual({
      turnSeq: 1,
      files: [{ path: '新建.txt', created: true }, { path: '修改.txt', created: false }],
      missing: ['旧记录.txt'],
      commands: true
    })
    t.apply(ev('user', { seq: 9 }))
    expect(target.value).toBeUndefined()
    t.apply(ev('turn_end', { seq: 10, reason: 'done' }))
    expect(target.value).toBeUndefined()
  })

  it('回放撤销结果后收起入口，下一轮仍能撤销，旧日志、关闭的分支没有入口', () => {
    const t = new Transcript()
    const edits = (seq: number, created: boolean) => [
      ev('user', { seq, t: 10 + seq }),
      ev('tool', { seq: seq + 1, id: 'a', name: 'write_file', status: 'ok', file: 'file.txt', ...(created && { created, after: 'a' }) }),
      ev('turn_end', { seq: seq + 2, t: 11 + seq })
    ]
    edits(1, false).forEach((e) => t.apply(e))
    expect(t.undoTarget).toBeUndefined()
    edits(4, true).forEach((e) => t.apply(e))
    expect(t.undoTarget?.undo?.turnSeq).toBe(4)
    const result = ev('undo', { seq: 7, t: 210, turn_seq: 4, restored: ['file.txt'], skipped: { 'b.py': '后来被修改' }, commands: true })
    t.apply(result)
    t.apply(result)
    expect(t.undoTarget).toBeUndefined()
    expect(t.items.filter((item) => item.kind === 'undo')).toMatchObject([{ result }])
    expect(buildTrace(t.events)[1]).toMatchObject({ duration: 1, undo: result })
    edits(8, true).forEach((e) => t.apply(e))
    expect(t.undoTarget?.undo?.turnSeq).toBe(8)
    t.apply(ev('branch_closed', { seq: 11 }))
    expect(t.undoTarget).toBeUndefined()
  })

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

  it('派助手去查：卡片带报告；助手的用量算进总数，不算主 Agent 的一步', () => {
    const t = new Transcript()
    t.apply(ev('start', { seq: 0 }))
    t.apply(ev('user', { seq: 1, text: '查查', t: 1 }))
    const calls = [
      { id: 'a', name: 'explore', arguments: '{"task":"登录在哪处理"}' },
      { id: 'b', name: 'explore', arguments: '{"task":"怎么跑测试"}' }
    ]
    t.apply(ev('assistant', { seq: 2, text: '分头查。', tool_calls: calls }))
    t.apply(ev('usage', { seq: 3, prompt: 1000, cache_hit: 900, completion: 20 }))
    t.apply(ev('tool_start', { id: 'a', name: 'explore' }))
    t.apply(ev('tool_start', { id: 'b', name: 'explore' }))
    t.apply(ev('tool', { seq: 4, id: 'a', name: 'explore', status: 'ok', content: '在 **auth.py:12**', sub: 's1', model: 'fast', steps: 3 }))
    t.apply(ev('usage', { seq: 5, prompt: 500, cache_hit: 100, completion: 30, purpose: 'explore', step: 0 }))
    const cards = t.items.filter((i): i is ToolItem => i.kind === 'tool')
    expect(cards.map((c) => c.action)).toEqual(['派助手去查「登录在哪处理」', '派助手去查「怎么跑测试」'])
    expect(cards[0].sub).toEqual({ model: 'fast', steps: 3 })
    expect(cards[1].status).toBe('running')
    expect(t.steps).toEqual([{ prompt: 1000, hit: 900 }])
    expect(t.usage.prompt).toBe(1500)
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
