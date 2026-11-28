import { describe, expect, it } from 'vitest'
import type { EngineOption, ModelGroup } from '../../../shared/agent'
import { AILA, engineOf, modelName, pickLabel, stillLoading, switchKind, taskPick, validPick } from './models'

const group = (engine: ModelGroup['engine'], title: string, models: ModelGroup['models'], extra: Partial<ModelGroup> = {}): ModelGroup => ({
  engine, title, ready: true, logged_in: true, problem: '', hints: [], can_login: true, installs_adapter: false, loading: false, models, ...extra
})

const groups: ModelGroup[] = [
  group('aila', '艾拉（你填的大模型）', [{ value: '', name: 'deepseek-flash', default: true }]),
  group('codex', 'ChatGPT', [{ value: 'gpt-6.2', name: 'GPT-6.2', default: true }, { value: 'gpt-6-luna', name: 'GPT-6 Luna' }]),
  group('claude', 'Claude', [{ value: '', name: '默认模型', default: true }], { ready: false, logged_in: false, problem: '还没登录' })
]

describe('选模型', () => {
  it('名字按账号和模型显示', () => {
    expect(modelName(groups, AILA)).toBe('deepseek-flash')
    expect(pickLabel(groups, { engine: 'codex', value: 'gpt-6-luna' })).toBe('ChatGPT · GPT-6 Luna')
    expect(modelName(groups, { engine: 'codex', value: 'gpt-7' })).toBe('gpt-7') // 列表里没有：用值
  })

  it('同一家直接换，换了一家开新任务接着做', () => {
    expect(switchKind({ engine: 'codex', value: 'gpt-6.2' }, { engine: 'codex', value: 'gpt-6-luna' })).toBe('option')
    expect(switchKind({ engine: 'codex', value: 'gpt-6.2' }, { engine: 'codex', value: 'gpt-6.2' })).toBe('none')
    expect(switchKind(AILA, AILA)).toBe('none')
    expect(switchKind({ engine: 'codex', value: 'gpt-6.2' }, { engine: 'claude', value: '' })).toBe('handoff')
    expect(switchKind(AILA, { engine: 'codex', value: 'gpt-6.2' })).toBe('handoff')
  })

  it('任务现在用的模型：本机 Agent 看它的模型选项', () => {
    const options: EngineOption[] = [{ id: 'effort', name: '思考', category: 'thought_level', current: 'high', choices: [] },
      { id: 'model', name: 'Model', category: 'model', current: 'gpt-6-luna', choices: [] }]
    expect(taskPick('codex', options)).toEqual({ engine: 'codex', value: 'gpt-6-luna' })
    expect(taskPick(null, options)).toEqual(AILA)
    expect(taskPick('claude', [])).toEqual({ engine: 'claude', value: '' })
  })

  it('新任务执行者由选中的模型决定', () => {
    expect(engineOf(AILA)).toBeNull()
    expect(engineOf({ engine: 'claude', value: '' })).toBe('claude')
  })

  it('记下的选择不能用了就退回去', () => {
    expect(validPick(groups, { engine: 'claude', value: '' })).toEqual(AILA) // 没登录
    expect(validPick(groups, { engine: 'codex', value: 'gone' })).toEqual({ engine: 'codex', value: 'gpt-6.2' }) // 模型没了：用它默认的
    expect(validPick(groups, { engine: 'codex', value: 'gpt-6-luna' })).toEqual({ engine: 'codex', value: 'gpt-6-luna' })
    expect(stillLoading(groups)).toBe(false)
    expect(stillLoading([{ ...groups[1], loading: true }])).toBe(true)
  })
})
