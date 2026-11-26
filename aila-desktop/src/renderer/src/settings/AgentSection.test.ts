/** 在 Vue 的内存渲染器里操作真实设置组件，检查异步加载和删除反馈；不控制浏览器。 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createRenderer, h, KeepAlive, nextTick, type App } from 'vue'
import AgentSection from './sections/AgentSection.vue'

const api = vi.hoisted(() => ({ loadAgentRules: vi.fn(), removeAgentRule: vi.fn() }))
vi.mock('./agentRules', () => api)
const computer = vi.hoisted(() => ({
  loadComputerSettings: vi.fn(async () => ({ ok: true, data: { computer: { inventory: true } } })),
  saveComputerInventory: vi.fn(),
  loadComputerContext: vi.fn()
}))
vi.mock('./computer', () => computer)
// 定时任务那张卡片要桌面端的接口：这里当成不在桌面端（不显示），只测编程设置本身
vi.mock('../platform/bridge', () => ({ bridge: null, isDesktop: false }))
// 外部工具（MCP）那张卡片单独测；这里只要它不去连服务
vi.mock('./mcp', () => {
  const empty = async () => ({ ok: true, data: [] })
  return { loadMcp: empty, previewMcp: empty, importMcp: empty, restartMcp: empty, disableMcp: empty, removeMcp: empty, forgetMcpTool: empty, MCP_EXAMPLE: '' }
})

interface ViewNode {
  tag: string
  text: string
  children: ViewNode[]
  parent: ViewNode | null
  props: Record<string, any>
  checked: boolean
  addEventListener: (event: string, callback: (event: any) => void) => void
}
const node = (tag: string, text = ''): ViewNode => ({ tag, text, children: [], parent: null, props: {}, checked: false, addEventListener: () => {} })
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

let app: App | null = null
const text = (target: ViewNode): string => (target.tag === '#comment' ? '' : target.text) + target.children.map(text).join('')
function find(target: ViewNode, test: (item: ViewNode) => boolean): ViewNode | undefined {
  return test(target) ? target : target.children.map((child) => find(child, test)).find(Boolean)
}
async function settle(): Promise<void> {
  await Promise.resolve()
  await Promise.resolve()
  await nextTick()
}
async function mount(): Promise<ViewNode> {
  const root = node('root')
  app = renderer.createApp({ render: () => h(KeepAlive, null, { default: () => h(AgentSection) }) })
  app.mount(root)
  await settle()
  return root
}
afterEach(() => { app?.unmount(); app = null; vi.resetAllMocks() })
beforeEach(() => {
  computer.loadComputerSettings.mockResolvedValue({ ok: true, data: { computer: { inventory: true } } })
})

describe('编程设置组件', () => {
  it('电脑规则显示独立的电脑范围，删除时保留原始范围键', async () => {
    api.loadAgentRules.mockResolvedValue({ ok: true, data: [
      { project: 'computer', commands: ['Start-Process notepad.exe'] },
      { project: 'C:/项目', commands: ['npm test'] }
    ] })
    api.removeAgentRule.mockResolvedValue({ ok: true })
    const root = await mount()
    expect(text(root)).not.toContain('computer')
    const button = find(root, (item) => item.props['aria-label'] === '删掉 电脑 记住的命令：Start-Process notepad.exe')!
    expect(button).toBeDefined()
    await button.props.onClick()
    await settle()
    expect(api.removeAgentRule).toHaveBeenCalledWith('computer', 'Start-Process notepad.exe')
    expect(text(root)).not.toContain('Start-Process notepad.exe')
    expect(text(root)).toContain('npm test')
  })

  it('电脑环境默认打开，页内预览具体内容，关闭后保存且刷新预览', async () => {
    api.loadAgentRules.mockResolvedValue({ ok: true, data: [] })
    computer.loadComputerContext.mockResolvedValueOnce({ ok: true, data: { context: '系统：Windows\n程序：记事本\n工具：git', inventory: true } })
      .mockResolvedValueOnce({ ok: true, data: { context: '系统：Windows\n文档：C:/Docs', inventory: false } })
    computer.saveComputerInventory.mockResolvedValue({ ok: true, data: { computer: { inventory: false } } })
    const root = await mount()
    const toggle = find(root, (item) => item.tag === 'input' && item.props.type === 'checkbox')!
    expect(toggle.checked).toBe(true)
    expect(toggle.props.disabled).toBe(false)
    const preview = find(root, (item) => item.tag === 'button' && item.props['aria-controls'] === 'computer-context-preview')!
    await preview.props.onClick()
    await settle()
    expect(preview.props['aria-expanded']).toBe(true)
    expect(text(root)).toContain('程序：记事本')
    toggle.props['onUpdate:modelValue'](false)
    await toggle.props.onChange()
    await settle()
    expect(computer.saveComputerInventory).toHaveBeenCalledWith(false)
    expect(text(root)).toContain('已有任务的环境说明保持原样')
    expect(text(root)).not.toContain('程序：记事本')
    expect(text(root)).toContain('文档：C:/Docs')
  })

  it('电脑环境保存失败会还原开关并保留错误，预览错误可单独重试', async () => {
    api.loadAgentRules.mockResolvedValue({ ok: true, data: [] })
    computer.saveComputerInventory.mockResolvedValue({ ok: false, error: '设置没有写入' })
    computer.loadComputerContext.mockResolvedValueOnce({ ok: false, error: '环境探测暂时不可用' })
      .mockResolvedValueOnce({ ok: true, data: { context: '系统：Windows', inventory: true } })
    const root = await mount()
    const toggle = find(root, (item) => item.tag === 'input' && item.props.type === 'checkbox')!
    toggle.props['onUpdate:modelValue'](false)
    await toggle.props.onChange()
    await settle()
    expect(toggle.checked).toBe(true)
    expect(text(root)).toContain('设置没有写入')
    await find(root, (item) => item.props['aria-controls'] === 'computer-context-preview')!.props.onClick()
    await settle()
    expect(text(root)).toContain('环境探测暂时不可用')
    await find(root, (item) => item.tag === 'button' && text(item) === '重新读取')!.props.onClick()
    await settle()
    expect(text(root)).toContain('系统：Windows')
    expect(text(root)).toContain('设置没有写入')
  })
  it('没有规则时显示空状态和完整权限说明', async () => {
    api.loadAgentRules.mockResolvedValue({ ok: true, data: [] })
    const root = await mount()
    expect(text(root)).toContain('还没有记住的命令')
    for (const mode of ['只读', '先问再改', '工作区内自动', '危险命令不管哪种模式都要问']) expect(text(root)).toContain(mode)
  })

  it('按项目显示命令，删除一条后保留别的项目并给出反馈', async () => {
    api.loadAgentRules.mockResolvedValue({ ok: true, data: [
      { project: 'C:\\one', commands: ['npm test'] }, { project: 'C:\\two', commands: ['npm run build'] }
    ] })
    api.removeAgentRule.mockResolvedValue({ ok: true })
    const root = await mount()
    expect(text(root)).toContain('C:\\one')
    const button = find(root, (item) => item.tag === 'button' && String(item.props['aria-label']).includes('npm test'))!
    await button.props.onClick()
    await settle()
    expect(api.removeAgentRule).toHaveBeenCalledWith('C:\\one', 'npm test')
    expect(text(root)).not.toContain('npm test')
    expect(text(root)).toContain('npm run build')
    expect(text(root)).toContain('下次执行时按当前权限模式判断')
  })

  it('删除失败时保留原命令，错误用 alert 显示且可以重试', async () => {
    api.loadAgentRules.mockResolvedValue({ ok: true, data: [{ project: 'x', commands: ['npm test'] }] })
    api.removeAgentRule.mockResolvedValue({ ok: false, error: '没有这条记住的命令，可能已经删掉了。' })
    const root = await mount()
    const button = find(root, (item) => item.tag === 'button' && String(item.props['aria-label']).includes('npm test'))!
    await button.props.onClick()
    await settle()
    expect(text(root)).toContain('npm test')
    expect(text(find(root, (item) => item.props.role === 'alert')!)).toContain('可能已经删掉了')
    expect(button.props.disabled).toBe(false)
  })

  it('断连时显示错误，刷新后能恢复为空列表', async () => {
    api.loadAgentRules.mockResolvedValueOnce({ ok: false, error: '连不上 AI 服务' }).mockResolvedValueOnce({ ok: true, data: [] })
    const root = await mount()
    expect(text(root)).toContain('连不上 AI 服务')
    const button = find(root, (item) => item.tag === 'button' && text(item) === '刷新')!
    await button.props.onClick()
    await settle()
    expect(text(root)).not.toContain('连不上 AI 服务')
    expect(text(root)).toContain('还没有记住的命令')
  })
})
