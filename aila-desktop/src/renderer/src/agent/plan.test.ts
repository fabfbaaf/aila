import { describe, expect, it } from 'vitest'
import type { AgentEvent } from '../../../shared/agent'
import { canContinue, canRedo, planState } from './plan'

const ev = (type: string, extra: Record<string, unknown> = {}): AgentEvent => ({ type, ...extra })
const PLAN = { goal: '加打折', stages: [{ title: '写函数', goal: 'g1', verify: 'v1' }, { title: '接结算', goal: 'g2', verify: 'v2' }] }

describe('planState', () => {
  it('没有计划是 null；交了计划等你决定', () => {
    expect(planState([ev('user', { text: 'hi' })])).toBeNull()
    const p = planState([ev('tool', { id: 'c1', name: 'propose_plan', status: 'ok', plan: PLAN })])
    expect(p?.decision).toBe('proposed')
    expect(p?.toolId).toBe('c1')
    expect(p?.stages.map((s) => s.status)).toEqual(['pending', 'pending'])
    expect(canContinue(p, false)).toBe(false)
  })

  it('批准、开始、完成、卡住、停下、做完', () => {
    const events = [
      ev('tool', { id: 'c1', name: 'propose_plan', status: 'ok', plan: PLAN }),
      ev('plan_decision', { approved: true, pause_each: true }),
      ev('user', { plan_stage: 0, title: '第 1/2 阶段：写函数' }),
      ev('tool', { name: 'stage_done', stage: 0, summary: '好了' }),
      ev('plan_pause', { reason: 'pause_each', text: '等你看一眼', stage: 1 })
    ]
    let p = planState(events)!
    expect(p.decision).toBe('approved')
    expect(p.pauseEach).toBe(true)
    expect(p.stages[0]).toMatchObject({ status: 'done', summary: '好了' })
    expect(p.current).toBe(1)
    expect(p.paused?.reason).toBe('pause_each')
    expect(canContinue(p, false)).toBe(true)
    expect(canContinue(p, true)).toBe(false)

    p = planState([...events, ev('user', { plan_stage: 1 }), ev('tool', { name: 'stage_blocked', stage: 1, summary: '缺信息' })])!
    expect(p.paused).toBeNull()
    expect(p.stages[1].status).toBe('blocked')

    p = planState([...events, ev('user', { plan_stage: 1 }), ev('tool', { name: 'stage_done', stage: 1, summary: '接好了' }), ev('plan_end')])!
    expect(p.finished).toBe(true)
    expect(p.current).toBeNull()
    expect(canContinue(p, false)).toBe(false)
  })

  it('新交的计划代替旧的；没要的不能接着做；失败的 stage_done 不算', () => {
    const p = planState([
      ev('tool', { id: 'a', name: 'propose_plan', plan: PLAN }),
      ev('plan_decision', { approved: false }),
      ev('tool', { id: 'b', name: 'propose_plan', plan: { goal: '新的', stages: [{ title: '一步', goal: '', verify: '' }] } }),
      ev('tool', { name: 'stage_done', status: 'error', content: '现在没有在做的阶段' })
    ])!
    expect(p.goal).toBe('新的')
    expect(p.toolId).toBe('b')
    expect(p.stages[0].status).toBe('pending')
    expect(planState([ev('tool', { id: 'a', name: 'propose_plan', plan: PLAN }), ev('plan_decision', { approved: false })])!.decision).toBe('rejected')
  })

  it('服务重启停在做到一半的阶段：能接着做，也能重做；你的验收记在刚做完的阶段上', () => {
    const events = [
      ev('tool', { id: 'c1', name: 'propose_plan', status: 'ok', plan: PLAN }),
      ev('plan_decision', { approved: true, pause_each: true }),
      ev('user', { plan_stage: 0 }),
      ev('tool', { name: 'stage_done', stage: 0, summary: '好了' }),
      ev('acceptance', { verdict: 'accepted' }),
      ev('user', { plan_stage: 1 }),
      ev('acceptance', { verdict: 'rejected' }), // 阶段做到一半时的验收不算在哪个阶段上
      ev('plan_pause', { reason: 'restart', text: '服务重启了，第 2 阶段做到一半停了', stage: 1 })
    ]
    let p = planState(events)!
    expect(p.stages.map((s) => s.accepted)).toEqual([true, null])
    expect(p.paused?.reason).toBe('restart')
    expect(canContinue(p, false) && canRedo(p, false)).toBe(true)
    expect(canRedo(p, true)).toBe(false)
    p = planState([...events, ev('stage_reset', { stage: 1, restored: ['cart.py'] })])!
    expect(p.stages[1].status).toBe('pending')
    expect(p.current).toBe(1)
    expect(p.paused).toBeNull()
    expect(canRedo(p, false)).toBe(false) // 还没开始，不用重做
  })
})
