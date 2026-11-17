/** 内存渲染真实预算组件，验证未知价格、零上限、输入校验和保存期间切换会话。 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createRenderer, h, nextTick, reactive, type App } from 'vue'
import type { AgentBudgetEvent } from '../../../shared/agent'
import BudgetControls from './BudgetControls.vue'

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
}

function node(tag: string, text = ''): ViewNode {
  const listeners = new Map<string, (event: any) => void>()
  return {
    tag, text, value: '', children: [], parent: null, props: {}, listeners,
    addEventListener: (event, callback) => { listeners.set(event, callback) },
    getRootNode: () => ({}), focus: vi.fn(), select: vi.fn()
  }
}

const renderer = createRenderer<ViewNode, ViewNode>({
  createElement: (tag) => node(tag),
  createText: (text) => node('#text', text),
  createComment: (text) => node('#comment', text),
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

let app: App | undefined
const text = (target: ViewNode): string => (target.tag === '#comment' ? '' : target.text) + target.children.map(text).join('')
function find(target: ViewNode, test: (item: ViewNode) => boolean): ViewNode | undefined {
  return test(target) ? target : target.children.map((child) => find(child, test)).find(Boolean)
}
async function settle(): Promise<void> {
  await Promise.resolve()
  await Promise.resolve()
  await nextTick()
}
function mount(change: (sid: string, amount: number | null) => Promise<AgentBudgetEvent>) {
  vi.stubGlobal('Document', class {})
  vi.stubGlobal('ShadowRoot', class {})
  const state = reactive({ sid: 's1', cost: null as number | null, budget: 0 as number | null })
  const changed = vi.fn()
  const root = node('root')
  app = renderer.createApp({ render: () => h(BudgetControls, { ...state, change, onChanged: changed }) })
  app.mount(root)
  return { state, changed, root }
}
async function open(root: ViewNode): Promise<void> {
  await find(root, (item) => item.tag === 'button')!.props.onClick()
  await settle()
}
async function type(root: ViewNode, value: string): Promise<void> {
  const input = find(root, (item) => item.tag === 'input')!
  input.value = value
  input.listeners.get('input')!({ target: input })
  await settle()
}
const submit = (root: ViewNode): Promise<void> => find(root, (item) => item.tag === 'form')!.props.onSubmit({ preventDefault() {} })
afterEach(() => { app?.unmount(); app = undefined; vi.unstubAllGlobals() })

describe('页内预算表单', () => {
  it('未知价格不显示零费用，已设零上限仍可以编辑', async () => {
    const { root, state } = mount(vi.fn())
    expect(text(root)).toContain('价格未知 / 上限 ¥0.00')
    expect(text(root)).not.toContain('已花 ¥0.00')
    await open(root)
    expect(text(root)).toContain('无法完整计价')
    expect(find(root, (item) => item.tag === 'input')?.value).toBe('0')
    state.cost = 0.004
    state.budget = 0.01
    await settle()
    expect(text(root)).toContain('已花 ¥0.004 / 上限 ¥0.01')
  })

  it('非法金额留在表单提示，留空以 null 保存', async () => {
    const event: AgentBudgetEvent = { type: 'budget', seq: 3, amount: null }
    const change = vi.fn(async () => event)
    const { root, changed } = mount(change)
    await open(root)
    await type(root, 'Infinity')
    await submit(root)
    expect(change).not.toHaveBeenCalled()
    expect(text(find(root, (item) => item.props.role === 'alert')!)).toContain('有限的非负金额')
    await type(root, '')
    await submit(root)
    await settle()
    expect(change).toHaveBeenCalledWith('s1', null)
    expect(changed).toHaveBeenCalledWith('s1', event)
    expect(find(root, (item) => item.tag === 'form')).toBeUndefined()
  })

  it('保存请求不会重复发送，切换会话后不恢复旧表单和旧错误', async () => {
    let reject!: (error: Error) => void
    const change = vi.fn(() => new Promise<AgentBudgetEvent>((_resolve, failed) => { reject = failed }))
    const { root, state, changed } = mount(change)
    await open(root)
    await type(root, '0.5')
    const first = submit(root)
    await submit(root)
    expect(change).toHaveBeenCalledTimes(1)
    state.sid = 's2'
    state.budget = 2
    await settle()
    expect(find(root, (item) => item.tag === 'form')).toBeUndefined()
    reject(new Error('旧会话的请求失败'))
    await first
    await settle()
    expect(changed).not.toHaveBeenCalled()
    expect(text(root)).toContain('上限 ¥2.00')
    expect(text(root)).not.toContain('旧会话的请求失败')
  })

  it('保存后返回旧会话的标识，切换后的金额由新会话的数据决定', async () => {
    let resolve!: (event: AgentBudgetEvent) => void
    const change = vi.fn(() => new Promise<AgentBudgetEvent>((done) => { resolve = done }))
    const { root, state, changed } = mount(change)
    await open(root)
    await type(root, '0')
    const request = submit(root)
    state.sid = 's2'
    state.budget = 1
    await settle()
    const event: AgentBudgetEvent = { type: 'budget', seq: 7, amount: 0 }
    resolve(event)
    await request
    await settle()
    expect(changed).toHaveBeenCalledWith('s1', event)
    expect(text(root)).toContain('上限 ¥1.00')
    expect(find(root, (item) => item.tag === 'form')).toBeUndefined()
  })
})
