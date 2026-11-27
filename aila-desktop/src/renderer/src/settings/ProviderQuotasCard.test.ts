/** 用 Vue 内存渲染器验证供应商进度和预测在同一面板里切换。 */
import { afterEach, expect, it, vi } from 'vitest'
import { createRenderer, nextTick, type App } from 'vue'
import ProviderQuotasCard from './sections/ProviderQuotasCard.vue'
import type { ProviderQuota, QuotaWindow } from '../../../shared/providerQuotas'

const api = vi.hoisted(() => ({ providerQuotas: vi.fn() }))
vi.mock('../platform/bridge', () => ({ bridge: api }))

interface Node {
  tag: string
  value: string
  children: Node[]
  parent: Node | null
  props: Record<string, unknown>
}
const node = (tag: string, value = ''): Node => ({ tag, value, children: [], parent: null, props: {} })
const render = createRenderer<Node, Node>({
  createElement: node,
  createText: (value) => node('#text', value),
  createComment: () => node('#comment'),
  setText: (el, text) => { el.value = text },
  setElementText: (el, text) => { el.value = text; el.children = [] },
  patchProp: (el, prop, _old, value) => { el.props[prop] = value },
  parentNode: (el) => el.parent,
  nextSibling: (el) => el.parent?.children[el.parent.children.indexOf(el) + 1] ?? null,
  insert: (el, parent, anchor) => {
    if (el.parent) el.parent.children.splice(el.parent.children.indexOf(el), 1)
    el.parent = parent
    const index = anchor ? parent.children.indexOf(anchor) : -1
    parent.children.splice(index < 0 ? parent.children.length : index, 0, el)
  },
  remove: (el) => {
    if (el.parent) el.parent.children.splice(el.parent.children.indexOf(el), 1)
    el.parent = null
  }
})

function find(root: Node, predicate: (item: Node) => boolean): Node | undefined {
  return predicate(root) ? root : root.children.map((child) => find(child, predicate)).find(Boolean)
}
const text = (root: Node): string => root.value + root.children.map(text).join('')
let app: App | undefined
afterEach(() => { app?.unmount(); vi.useRealTimers() })

it.each([false, true])('用量总览（紧凑模式 %s）只展开选中厂商的进度和燃尽时间', async (compact) => {
  vi.useFakeTimers()
  vi.setSystemTime(new Date('2026-11-27T05:00:00Z'))
  const now = Date.now() / 1000
  const codex: QuotaWindow = {
    id: 'primary', label: 'Codex', remaining: 32, total: 100, unit: '%', reset_at: now + 7200,
    minutes: 300, observed_at: now, stale: false, forecast: { at: now + 5400, within_cycle: true }, source: 'automatic',
  }
  const provider = (id: string, name: string, entry: QuotaWindow): ProviderQuota => ({
    id, name, windows: [entry], status: 'ready', linked: false,
  })
  api.providerQuotas.mockResolvedValue({ ok: true, data: [
    provider('codex', 'Codex', codex),
    provider('grok', 'Grok', { ...codex, label: '月额度', remaining: 820, unit: 'credits', total: 1000, forecast: null }),
    provider('custom', '其他工具', { ...codex, label: '点数', remaining: 20, unit: '积分', total: 50, source: 'manual' }),
  ] })
  const root = node('root')
  const manage = vi.fn()
  const prefix = compact ? 'workspace-quota' : 'quota'
  app = render.createApp(ProviderQuotasCard, { compact, onManage: manage })
  app.mount(root)
  for (let i = 0; i < 6; i++) await nextTick()

  const button = (id: string) => find(root, (n) => n.props.id === prefix + '-tab-' + id)!
  const details = () => find(root, (n) => n.props.id === prefix + '-details')!
  const click = (id: string) => (button(id).props.onClick as () => void)()
  expect(text(button('codex'))).toContain('32%')
  expect(text(button('grok'))).toContain('820 credits')
  expect(text(button('custom'))).toContain('20 积分')
  expect(text(root)).toContain('2 小时 0 分后重置')
  expect(details()).toBeUndefined()

  click('codex')
  await nextTick()
  expect(button('codex').props['aria-expanded']).toBe(true)
  expect(details().props['aria-labelledby']).toBe(prefix + '-tab-codex')
  expect(find(details(), (n) => n.tag === 'progress')?.props.value).toBe(32)
  expect(text(details())).toContain('1 小时 30 分后用完')

  click('grok')
  await nextTick()
  expect(button('codex').props['aria-expanded']).toBe(false)
  expect(details().props['aria-labelledby']).toBe(prefix + '-tab-grok')
  expect(find(details(), (n) => n.tag === 'progress')?.props.value).toBe(820)
  expect(text(details())).toContain('记录到用量变化后预测')

  click('custom')
  await nextTick()
  const manageButton = find(details(), (n) => n.tag === 'button' && text(n) === '管理额度')
  expect(text(details()).includes('更新记录')).toBe(!compact)
  expect(Boolean(manageButton)).toBe(compact)
  if (manageButton) {
    (manageButton.props.onClick as () => void)()
    expect(manage).toHaveBeenCalledOnce()
  }
})
