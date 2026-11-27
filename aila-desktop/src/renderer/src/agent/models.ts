/**
 * 选模型的纯逻辑：对话和编程任务输入框右下角的那个按钮（ModelPicker.vue）都用这一份。
 *
 * 只按模型选，不用先想「交给 Codex 还是 Claude」：模型按账号分组（艾拉自己的、ChatGPT、Claude……），
 * 选了哪个就是谁来做。任务做到一半换同一家的模型直接换；换了一家，它们的对话记录不通，开一个新任务接着做（前情由 AI 服务整理）。
 */
import type { AgentEngine, EngineOption, ModelGroup } from '../../../shared/agent'
import { CHAT_BRAINS, type ChatBrain } from '../../../shared/settings'

export interface ModelPick {
  engine: 'aila' | AgentEngine
  /** 空：它自己默认的 */
  value: string
}

export const AILA: ModelPick = { engine: 'aila', value: '' }

export const samePick = (a: ModelPick, b: ModelPick): boolean => a.engine === b.engine && a.value === b.value

/** 按钮上、任务页那一行显示的名字：「模型名」，找不到就用值，空值是「默认模型」 */
export function modelName(groups: ModelGroup[], pick: ModelPick, fallback = '默认模型'): string {
  const group = groups.find((g) => g.engine === pick.engine)
  const model = group?.models.find((m) => m.value === pick.value)
  return model?.name || pick.value || fallback
}

/** 「账号 · 模型」：换模型接着做时任务页上那一行用 */
export function pickLabel(groups: ModelGroup[], pick: ModelPick): string {
  const group = groups.find((g) => g.engine === pick.engine)
  const name = modelName(groups, pick)
  return group ? `${group.title} · ${name}` : name
}

/** 任务里换模型怎么换：一样就不动；同一家换模型（艾拉自己的只有一个，不用动）；换了一家要开新任务接着做 */
export function switchKind(current: ModelPick, next: ModelPick): 'none' | 'option' | 'handoff' {
  if (current.engine !== next.engine) return 'handoff'
  if (next.engine === 'aila' || current.value === next.value) return 'none'
  return 'option'
}

/** 任务现在用的模型：本机 Agent 看它的模型选项（还没连上时是你选过的），艾拉自己就是 aila */
export function taskPick(engine: AgentEngine | null | undefined, options: EngineOption[]): ModelPick {
  if (!engine) return AILA
  const model = options.find((o) => o.category === 'model') ?? options.find((o) => o.id === 'model')
  return { engine, value: model?.current ?? '' }
}

/** 新任务交给谁：艾拉自己是 null */
export const engineOf = (pick: ModelPick): AgentEngine | null => (pick.engine === 'aila' ? null : pick.engine)

/** 对话只保存已纳入对话链路的引擎。 */
export function chatPick(settings: { chatBrain?: ChatBrain; chatBrainModel?: string }): ModelPick {
  return settings.chatBrain ? { engine: settings.chatBrain, value: settings.chatBrainModel ?? '' } : AILA
}

export function chatUpdate(pick: ModelPick): { chatBrain: ChatBrain; chatBrainModel: string } | undefined {
  if (pick.engine === 'aila') return { chatBrain: '', chatBrainModel: '' }
  if (!CHAT_BRAINS.some((engine) => engine === pick.engine)) return undefined
  return { chatBrain: pick.engine as ChatBrain, chatBrainModel: pick.value }
}

/** 列表里还没问完的（loading）：过一会儿再拿一次 */
export const stillLoading = (groups: ModelGroup[]): boolean => groups.some((g) => g.loading)

/** 记下的选择还在不在列表里、能不能用：不能用的退回艾拉自己 */
export function validPick(groups: ModelGroup[], pick: ModelPick): ModelPick {
  const group = groups.find((g) => g.engine === pick.engine)
  if (!group || !group.ready) return AILA
  if (group.models.some((m) => m.value === pick.value)) return pick
  const fallback = group.models.find((m) => m.default) ?? group.models[0]
  return { engine: pick.engine, value: fallback?.value ?? '' }
}
