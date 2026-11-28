/**
 * 长任务的计划：从会话日志推出最新那份计划做到哪了（和 aila-ai/app/runtime/planning.py 的 plan_state 同一套规则）。
 * 计划在 propose_plan 的 tool 事件里（plan 字段）；你的决定是 plan_decision；每阶段开始的那句话是带 plan_stage 的 user 事件；
 * 她报告完成 / 卡住是 stage_done / stage_blocked 的 tool 事件（stage、summary）；停下、做完是 plan_pause / plan_end。
 * 重做某个阶段（先把它改过的文件退回去）是 stage_reset；阶段刚做完时你在交付卡上点的通过 / 退回（acceptance）记在那个阶段上。
 * 服务重启时做到一半：plan_pause 的 reason 是 restart。
 */
import type { AgentEvent } from '../../../shared/agent'

export type StageStatus = 'pending' | 'running' | 'done' | 'blocked'

export interface PlanStage {
  title: string
  goal: string
  verify: string
  status: StageStatus
  summary: string
  /** 你在交付卡上对这一阶段的验收：通过 true，退回修改 false，没看过 null */
  accepted: boolean | null
}

export interface PlanView {
  goal: string
  stages: PlanStage[]
  decision: 'proposed' | 'approved' | 'rejected'
  pauseEach: boolean
  current: number | null
  finished: boolean
  /** 最近一次停下的原因（plan_pause 之后还没接着做）；null：没停或者已经接着做了 */
  paused: { reason: string; text: string; stage: number } | null
  /** propose_plan 那个工具调用的 id：只有这一张计划卡片能批准，之前交的都被它代替了 */
  toolId: string
}

interface RawPlan {
  goal: string
  stages: { title: string; goal: string; verify: string }[]
}

function next(stages: PlanStage[]): number | null {
  const i = stages.findIndex((s) => s.status !== 'done')
  return i < 0 ? null : i
}

export function planState(events: readonly AgentEvent[]): PlanView | null {
  let state: PlanView | null = null
  let lastDone: number | null = null // 刚做完、下一阶段还没开始的那个：这时候的验收算在它上面
  for (const e of events) {
    if (e.type === 'tool' && e.name === 'propose_plan' && e.plan && typeof e.plan === 'object') {
      const raw = e.plan as RawPlan
      state = {
        goal: String(raw.goal ?? ''),
        stages: (raw.stages ?? []).map((s) => ({ title: String(s.title), goal: String(s.goal ?? ''), verify: String(s.verify ?? ''), status: 'pending', summary: '', accepted: null })),
        decision: 'proposed',
        pauseEach: false,
        current: null,
        finished: false,
        paused: null,
        toolId: String(e.id ?? '')
      }
      continue
    }
    if (!state) continue
    if (e.type === 'plan_decision') {
      state.decision = e.approved ? 'approved' : 'rejected'
      state.pauseEach = Boolean(e.pause_each)
      if (state.decision === 'approved' && state.current === null) state.current = next(state.stages)
    } else if (e.type === 'user' && typeof e.plan_stage === 'number' && state.decision === 'approved') {
      const n = e.plan_stage
      if (n >= 0 && n < state.stages.length) {
        state.current = n
        lastDone = null
        if (state.stages[n].status !== 'done') state.stages[n].status = 'running'
        state.paused = null
      }
    } else if (e.type === 'tool' && (e.name === 'stage_done' || e.name === 'stage_blocked') && typeof e.stage === 'number') {
      const n = e.stage
      if (n >= 0 && n < state.stages.length) {
        state.stages[n].status = e.name === 'stage_done' ? 'done' : 'blocked'
        state.stages[n].summary = String(e.summary ?? '')
        if (e.name === 'stage_done') {
          state.current = next(state.stages)
          lastDone = n
        }
      }
    } else if (e.type === 'stage_reset' && typeof e.stage === 'number' && state.decision === 'approved') {
      const n = e.stage
      if (n >= 0 && n < state.stages.length) {
        Object.assign(state.stages[n], { status: 'pending', summary: '', accepted: null })
        state.current = n
        lastDone = null
        state.paused = null
      }
    } else if (e.type === 'acceptance' && lastDone !== null) {
      state.stages[lastDone].accepted = e.verdict === 'accepted'
    } else if (e.type === 'plan_pause') {
      state.paused = { reason: String(e.reason ?? ''), text: String(e.text ?? ''), stage: Number(e.stage ?? 0) }
    }
  }
  if (state && state.decision === 'approved') {
    state.finished = state.stages.length > 0 && state.stages.every((s) => s.status === 'done')
    if (state.finished) state.current = null
  }
  return state
}

/** 计划能不能「接着做」：批准过、没做完、她没在做 */
export function canContinue(plan: PlanView | null, running: boolean): boolean {
  return !!plan && plan.decision === 'approved' && !plan.finished && !running
}

/** 能不能「重做这一阶段」：能接着做，而且当前阶段做到一半或者卡住了（改过的文件先退回到上一阶段做完时） */
export function canRedo(plan: PlanView | null, running: boolean): boolean {
  if (!canContinue(plan, running) || plan!.current === null) return false
  const status = plan!.stages[plan!.current]?.status
  return status === 'running' || status === 'blocked'
}
