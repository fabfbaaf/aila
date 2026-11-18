/** 真实任务页的预算交互回归：HTTP 与实时流乱序，以及金额输入不能触发工具确认快捷键。 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createRenderer, nextTick, type App } from 'vue'
import type { AgentBudgetEvent, AgentEvent, AgentSession, AgentSessionSummary } from '../../../shared/agent'
import type { AgentSource } from './source'
import type { Transcript } from './fold'
import AgentApp from './AgentApp.vue'

const fixture = vi.hoisted(() => ({ source: {} as AgentSource }))
vi.mock('./source', () => ({ createAgentSource: () => fixture.source }))

interface ViewNode {
  tag: string
  text: string
  value: string
  children: ViewNode[]
  parent: ViewNode | null
  props: Record<string, any>
  listeners: Map<string, (event: any) => void>
  addEventListener: (event: string, callback: (event: any) => void) => void
  getRootNode: () => object
  focus: () => void
  select: () => void
  close: () => void
}
function node(tag: string, text = ''): ViewNode {
  const listeners = new Map<string, (event: any) => void>()
  return {
    tag, text, value: '', children: [], parent: null, props: {}, listeners,
    addEventListener: (event, callback) => { listeners.set(event, callback) },
    getRootNode: () => ({}), focus: vi.fn(), select: vi.fn(), close: vi.fn()
  }
}
const renderer = createRenderer<ViewNode, ViewNode>({
  createElement: (tag) => node(tag), createText: (text) => node('#text', text), createComment: (text) => node('#comment', text),
  setText: (target, text) => { target.text = text },
  setElementText: (target, text) => { target.text = text; target.children = [] },
  parentNode: (target) => target.parent,
  nextSibling: (target) => target.parent?.children[target.parent.children.indexOf(target) + 1] ?? null,
  patchProp: (target, key, _before, after) => { target.props[key] = after },
  insert: (target, parent, anchor) => {
    if (target.parent) target.parent.children.splice(target.parent.children.indexOf(target), 1)
    target.parent = parent
    const index = anchor ? parent.children.indexOf(anchor) : -1
    parent.children.splice(index < 0 ? parent.children.length : index, 0, target)
  },
  remove: (target) => {
    if (target.parent) target.parent.children.splice(target.parent.children.indexOf(target), 1)
    target.parent = null
  }
})
function find(target: ViewNode, test: (item: ViewNode) => boolean): ViewNode | undefined {
  return test(target) ? target : target.children.map((child) => find(child, test)).find(Boolean)
}
const byId = (root: ViewNode, id: string): ViewNode => find(root, (item) => item.props.id === id)!
const byClass = (root: ViewNode, name: string): ViewNode => find(root, (item) => typeof item.props.class === 'string' && item.props.class.split(' ').includes(name))!
const viewText = (target: ViewNode): string => (target.tag === '#comment' ? '' : target.text) + target.children.map(viewText).join('')
function all(target: ViewNode, test: (item: ViewNode) => boolean): ViewNode[] {
  return [...(test(target) ? [target] : []), ...target.children.flatMap((child) => all(child, test))]
}
async function settle(): Promise<void> { for (let i = 0; i < 10; i++) await Promise.resolve(); await nextTick() }
function keydown(target: ViewNode, key: string, global: (event: any) => void): void {
  let stopped = false
  const event = { key, target, preventDefault: vi.fn(), stopPropagation: () => { stopped = true } }
  for (let n: ViewNode | null = target; n; n = n.parent) {
    const handlers = n.props.onKeydown
    for (const handler of Array.isArray(handlers) ? handlers : handlers ? [handlers] : []) handler(event)
    if (stopped) return
  }
  global(event)
}

const summary = (): AgentSessionSummary => ({
  id: 's1', cwd: 'C:/项目', title: '任务', mode: 'ask', model: 'mock', events: 2, running: true,
  usage: { prompt: 0, completion: 0, cache_hit: 0, cache_miss: 0 }, cost: null, budget: 0.01
})
let app: App | undefined
let root: ViewNode
let event: (sid: string, e: AgentEvent) => void
let globalKey: (event: any) => void
let debug: { t: { value: Transcript }; open: (sid: string) => Promise<void> }
beforeEach(async () => {
  vi.useFakeTimers()
  vi.stubGlobal('Document', class {})
  vi.stubGlobal('ShadowRoot', class {})
  vi.stubGlobal('HTMLTextAreaElement', class {})
  const host = {
    setTimeout, setInterval,
    addEventListener: (name: string, listener: (e: any) => void) => { if (name === 'keydown') globalKey = listener },
    removeEventListener: vi.fn()
  }
  vi.stubGlobal('window', host)
  fixture.source = {
    list: vi.fn(async () => [summary()]),
    get: vi.fn(async () => ({ ...summary(), events: [{ type: 'start', seq: 0, budget: 0.01 }, { type: 'user', seq: 1, t: 1, text: '做任务' }] })),
    setBudget: vi.fn(), screenshot: vi.fn(async () => 'data:image/png;base64,preview'),
    create: vi.fn(), memory: vi.fn(async () => []), send: vi.fn(), plan: vi.fn(async () => {}),
    watch: vi.fn(), projects: vi.fn(async () => []), models: vi.fn(async () => null),
    takeFocus: vi.fn(async () => null), confirm: vi.fn(async () => true), interrupt: vi.fn(async () => {}),
    onEvent: (cb: (sid: string, e: AgentEvent) => void) => { event = cb }, onStreamEnd: vi.fn(), onFocus: vi.fn()
  } as unknown as AgentSource
  root = node('root')
  app = renderer.createApp(AgentApp)
  app.mount(root)
  await settle()
  debug = (host as unknown as { __agent: typeof debug }).__agent
})
afterEach(() => { app?.unmount(); app = undefined; vi.useRealTimers(); vi.unstubAllGlobals() })

describe('任务页网页截图', () => {
  it('成功的截图事件可以在原工具卡片里展开预览', async () => {
    const screenshot = '1234567890abcdef1234567890abcdef.png'
    event('s1', { type: 'tool', seq: 2, id: 'shot', name: 'browser_screenshot', status: 'ok', content: '截图已保存', screenshot })
    await settle()
    expect(fixture.source.screenshot).not.toHaveBeenCalled()
    const header = byClass(root, 'head')
    expect(header.tag).toBe('button')
    expect(header.props['aria-expanded']).toBe(false)
    header.props.onClick()
    await settle()
    expect(fixture.source.screenshot).toHaveBeenCalledWith('s1', screenshot)
    expect(find(root, (item) => item.tag === 'img')?.props).toMatchObject({
      src: 'data:image/png;base64,preview',
      alt: '编程任务截取的网页'
    })
  })
})

describe('任务页预算', () => {
  it('保存先返回较大 seq 时补齐日志，晚到的流不会丢掉工具、用量或结束记录', async () => {
    const budget: AgentBudgetEvent = { type: 'budget', seq: 6, t: 3, amount: 0.02 }
    const middle: AgentEvent[] = [
      { type: 'assistant', seq: 2, text: '读到文件', tool_calls: [{ id: 'a', name: 'read_file', arguments: '{"path":"a.txt"}' }] },
      { type: 'usage', seq: 3, prompt: 10, completion: 5 },
      { type: 'tool', seq: 4, id: 'a', name: 'read_file', status: 'ok', content: '文件内容' },
      { type: 'turn_end', seq: 5, t: 2, reason: 'done', cost: 0.004 }
    ]
    let respond!: (session: AgentSession) => void
    vi.mocked(fixture.source.get).mockImplementationOnce(() => new Promise((resolve) => { respond = resolve }))
    vi.mocked(fixture.source.setBudget).mockResolvedValue(budget)
    await byClass(root, 'budget-button').props.onClick()
    await settle()
    const input = byId(root, 'task-budget-amount')
    input.value = '0.02'
    input.listeners.get('input')!({ target: input })
    await byId(root, 'task-budget-editor').props.onSubmit({ preventDefault() {} })
    expect(fixture.source.get).toHaveBeenCalledTimes(2)
    expect(debug.t.value.lastSeq).toBe(1)
    respond({ ...summary(), running: false, budget: 0.02, events: [...debug.t.value.events, ...middle, budget] })
    await settle()
    for (const e of middle) event('s1', e)
    event('s1', budget)
    await settle()
    expect(debug.t.value.lastSeq).toBe(6)
    expect(debug.t.value.budget).toBe(0.02)
    expect(debug.t.value.usage).toMatchObject({ prompt: 10, completion: 5 })
    expect(debug.t.value.items.filter((item) => item.kind === 'text')).toMatchObject([{ text: '读到文件' }])
    expect(debug.t.value.items.filter((item) => item.kind === 'tool')).toMatchObject([{ status: 'ok', content: '文件内容' }])
    expect(debug.t.value.items.filter((item) => item.kind === 'end')).toMatchObject([{ reason: 'done', cost: 0.004 }])
  })

  it('等待工具确认时，两处预算输入的 1/2/Esc 不批准工具也不打断，表单 Enter 仍可保存', async () => {
    event('s1', { type: 'confirm', seq: 2, id: 'c1', call_id: 'a', tool: 'run_command', can_remember: true })
    await settle()
    expect(debug.t.value.pending?.confirm?.id).toBe('c1')
    await byClass(root, 'budget-button').props.onClick()
    await settle()
    const input = byId(root, 'task-budget-amount')
    for (const key of ['1', '2', 'Enter']) keydown(input, key, globalKey)
    const saved: AgentBudgetEvent = { type: 'budget', seq: 3, amount: 0.01 }
    vi.mocked(fixture.source.setBudget).mockResolvedValue(saved)
    vi.mocked(fixture.source.get).mockResolvedValueOnce({ ...summary(), events: [...debug.t.value.events, saved] })
    await byId(root, 'task-budget-editor').props.onSubmit({ preventDefault() {} })
    await settle()
    expect(fixture.source.setBudget).toHaveBeenCalledWith('s1', 0.01)
    await byClass(root, 'budget-button').props.onClick()
    await settle()
    keydown(byId(root, 'task-budget-amount'), 'Escape', globalKey)
    await settle()
    expect(find(root, (item) => item.props.id === 'task-budget-editor')).toBeUndefined()
    await byClass(root, 'new').props.onClick()
    await settle()
    expect(debug.t.value.pending?.confirm?.id).toBe('c1')
    for (const key of ['1', '2', 'Escape']) keydown(byId(root, 'new-task-budget'), key, globalKey)
    expect(fixture.source.confirm).not.toHaveBeenCalled()
    expect(fixture.source.interrupt).not.toHaveBeenCalled()
    globalKey({ key: '1', target: root, preventDefault: vi.fn() })
    expect(fixture.source.confirm).toHaveBeenCalledWith('c1', true, false)
  })

  it('切换会话尚未返回时，旧预算保存回执不能抢回旧会话', async () => {
    let saved!: (event: AgentBudgetEvent) => void
    let loaded!: (session: AgentSession) => void
    vi.mocked(fixture.source.setBudget).mockImplementationOnce(() => new Promise((resolve) => { saved = resolve }))
    await byClass(root, 'budget-button').props.onClick()
    await settle()
    const saving = byId(root, 'task-budget-editor').props.onSubmit({ preventDefault() {} })
    vi.mocked(fixture.source.get).mockImplementationOnce(() => new Promise((resolve) => { loaded = resolve }))
    const switching = debug.open('s2')
    saved({ type: 'budget', seq: 2, amount: 0.01 })
    await saving
    await settle()
    expect(fixture.source.get).toHaveBeenCalledTimes(2)
    expect(fixture.source.get).toHaveBeenLastCalledWith('s2')
    loaded({ ...summary(), id: 's2', budget: 0.5, events: [{ type: 'start', seq: 0, budget: 0.5 }] })
    await switching
    await settle()
    expect(debug.t.value.budget).toBe(0.5)
    expect(byClass(root, 'budget-button').props.title).toContain('上限 ¥0.50')
  })
})

describe('电脑任务入口', () => {
  it('电脑命令记住按钮写清跨电脑任务的范围，项目命令保留项目范围', async () => {
    event('s1', { type: 'confirm', seq: 2, id: 'project-confirm', call_id: 'a', tool: 'run_command', can_remember: true })
    await settle()
    expect(viewText(root)).toContain('这条命令在这个项目里不用再问')
    vi.mocked(fixture.source.get).mockResolvedValue({
      ...summary(), id: 'computer-confirm', kind: 'computer', running: true,
      events: [{ type: 'start', seq: 0, mode: 'ask', kind: 'computer' }]
    })
    await debug.open('computer-confirm')
    event('computer-confirm', { type: 'confirm', seq: 1, id: 'computer-command', call_id: 'b', tool: 'run_command', can_remember: true })
    await settle()
    expect(viewText(root)).toContain('这条命令在电脑任务里不用再问')
    expect(viewText(root)).not.toContain('这条命令在这个项目里不用再问')
  })

  it('从自动和新分支切到电脑后只留两种权限，创建时不夹带项目、自动或分支状态', async () => {
    await byClass(root, 'new').props.onClick()
    await settle()
    const auto = find(root, (item) => item.tag === 'input' && item.props.value === 'auto')!
    auto.props['onUpdate:modelValue']('auto')
    byId(root, 'new-task-isolate').props['onUpdate:modelValue'](true)
    byId(root, 'new-task-plan').props['onUpdate:modelValue'](true)
    const computer = find(root, (item) => item.tag === 'input' && item.props.value === 'computer')!
    computer.props['onUpdate:modelValue']('computer')
    await settle()
    const picker = byClass(root, 'picker')
    expect(viewText(picker)).not.toContain('工作区内自动')
    expect(find(picker, (item) => item.props.id === 'new-task-isolate')).toBeUndefined()
    expect(viewText(picker)).toContain('先出方案再动手')
    const computerSession: AgentSession = {
      ...summary(), id: 'computer1', kind: 'computer', cwd: 'C:/Users/test', running: false,
      events: [{ type: 'start', seq: 0, mode: 'ask', kind: 'computer' }]
    }
    vi.mocked(fixture.source.create).mockResolvedValue({ ...computerSession, events: 1 })
    vi.mocked(fixture.source.get).mockResolvedValue(computerSession)
    await byClass(root, 'computer-start').props.onClick()
    await settle()
    expect(fixture.source.create).toHaveBeenCalledWith(undefined, 'ask', false, null, 'computer')
    expect(viewText(byClass(root, 'proj'))).toContain('电脑')
    expect(all(byClass(root, 'mode'), (item) => item.tag === 'option').map(viewText)).toEqual(['只读', '先问再改'])
    expect(find(root, (item) => item.tag === 'section' && item.props.class === 'memory')).toBeUndefined()
    const composer = find(root, (item) => item.tag === 'textarea')!
    composer.props['onUpdate:modelValue']('先整理测试文件夹')
    await settle()
    byClass(root, 'send').props.onClick()
    expect(fixture.source.send).toHaveBeenCalledWith('computer1', '先整理测试文件夹', false, true)
    // 电脑任务没有项目代码：不给「审查改动」，输入 /review 只提示
    expect(viewText(root)).not.toContain('审查改动')
    vi.mocked(fixture.source.send).mockClear()
    composer.props['onUpdate:modelValue']('/review')
    await settle()
    byClass(root, 'send').props.onClick()
    await settle()
    expect(fixture.source.send).not.toHaveBeenCalled()
    expect(viewText(root)).toContain('电脑任务没有项目代码可审')
  })

  it('电脑任务仍可查看和批准计划，阶段流显示在原任务页', async () => {
    const planEvents: AgentEvent[] = [
      { type: 'start', seq: 0, mode: 'ask', kind: 'computer' },
      { type: 'user', seq: 1, text: '整理测试文件夹', plan_request: true },
      { type: 'assistant', seq: 2, text: '', tool_calls: [{ id: 'p1', name: 'propose_plan', arguments: '{}' }] },
      { type: 'tool', seq: 3, id: 'p1', name: 'propose_plan', status: 'ok',
        plan: { goal: '整理测试文件夹', stages: [
          { title: '列出文件', goal: '只读盘点', verify: '列出将要移动的文件' },
          { title: '按类型整理', goal: '等待确认后移动', verify: '核对文件数量' }
        ] } },
      { type: 'turn_end', seq: 4, reason: 'done' }
    ]
    vi.mocked(fixture.source.get).mockResolvedValue({
      ...summary(), id: 'c2', kind: 'computer', running: false, events: planEvents
    })
    await debug.open('c2')
    await settle()
    expect(viewText(byClass(root, 'plan-card'))).toContain('列出文件')
    await byClass(root, 'go').props.onClick()
    expect(fixture.source.plan).toHaveBeenCalledWith('c2', 'approve', false)
    event('c2', { type: 'plan_decision', seq: 5, approved: true, pause_each: false })
    event('c2', { type: 'user', seq: 6, text: '开始盘点', plan_stage: 0, title: '第 1/2 阶段：列出文件' })
    await settle()
    expect(viewText(byClass(root, 'stage-line'))).toContain('第 1/2 阶段：列出文件')
    expect(viewText(byClass(root, 'plan-card'))).toContain('做到第 1/2 阶段')
  })
})
