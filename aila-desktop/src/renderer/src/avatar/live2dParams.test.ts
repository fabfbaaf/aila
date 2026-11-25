import { Vector3 } from 'three'
import { describe, expect, it } from 'vitest'
import { idsFromGroups, live2dParams, lookFrom, pickMotion } from './live2dParams'
import { emptyExpressions, type PoseState } from './types'

function pose(p: Partial<PoseState> = {}): PoseState {
  return {
    lookTarget: new Vector3(), headPitch: 0, headYaw: 0, headRoll: 0, bodyLean: 0, bodySway: 0, breath: 0,
    armRaise: 0, forearmSwing: 0, armsUp: 0, typing: 0, typingBob: 0, ...p
  }
}

describe('姿态、表情 → Live2D 参数', () => {
  it('不动的时候：正脸、睁眼、闭嘴', () => {
    const p = live2dParams(pose(), emptyExpressions(), { x: 0, y: 0 })
    expect(p.ParamAngleX).toBe(0)
    expect(p.ParamEyeLOpen).toBe(1)
    expect(p.ParamMouthOpenY).toBe(0)
    expect(p.ParamBreath).toBe(0.5)
  })

  it('点头、摇头、歪头换成角度，不超过 ±30', () => {
    expect(live2dParams(pose({ headPitch: 0.6 }), emptyExpressions(), { x: 0, y: 0 }).ParamAngleY).toBe(-30) // 低头
    expect(live2dParams(pose({ headYaw: 5 }), emptyExpressions(), { x: 1, y: 0 }).ParamAngleX).toBe(30)
    expect(live2dParams(pose({ headRoll: 0.25 }), emptyExpressions(), { x: 0, y: 0 }).ParamAngleZ).toBe(15)
  })

  it('眨眼闭眼，笑的时候眯眼、嘴角上扬，难过撇嘴', () => {
    expect(live2dParams(pose(), { ...emptyExpressions(), blink: 1 }, { x: 0, y: 0 }).ParamEyeLOpen).toBe(0)
    const happy = live2dParams(pose(), { ...emptyExpressions(), happy: 1 }, { x: 0, y: 0 })
    expect(happy.ParamEyeLOpen).toBeCloseTo(0.6)
    expect(happy.ParamMouthForm).toBeCloseTo(0.8)
    expect(happy.ParamEyeLSmile).toBe(1)
    expect(live2dParams(pose(), { ...emptyExpressions(), sad: 1 }, { x: 0, y: 0 }).ParamMouthForm).toBeCloseTo(-0.85)
  })

  it('说话：元音合成张嘴大小', () => {
    expect(live2dParams(pose(), { ...emptyExpressions(), aa: 0.8 }, { x: 0, y: 0 }).ParamMouthOpenY).toBeCloseTo(0.8)
    expect(live2dParams(pose(), { ...emptyExpressions(), ih: 1 }, { x: 0, y: 0 }).ParamMouthOpenY).toBeCloseTo(0.35)
  })

  it('视线：往右上看', () => {
    const look = lookFrom(1, 0.5, 1.2)
    expect(look.x).toBeGreaterThan(0.5)
    expect(look.y).toBeGreaterThan(0)
    expect(lookFrom(100, 0, 0).x).toBe(1) // 截在 -1..1
    const p = live2dParams(pose(), emptyExpressions(), look)
    expect(p.ParamEyeBallX).toBeCloseTo(look.x)
    expect(p.ParamAngleX).toBeGreaterThan(0)
  })
})

describe('按模型自己的设置', () => {
  it('张嘴、眨眼用 model3.json 里声明的参数（真央张嘴是 ParamA）；没声明就用标准名', () => {
    const ids = idsFromGroups([
      { Target: 'Parameter', Name: 'EyeBlink', Ids: ['ParamEyeLOpen', 'ParamEyeROpen'] },
      { Target: 'Parameter', Name: 'LipSync', Ids: ['ParamA'] }
    ])
    expect(ids.lipSync).toEqual(['ParamA'])
    const p = live2dParams(pose(), { ...emptyExpressions(), aa: 0.8, blink: 1 }, { x: 0, y: 0 }, ids)
    expect(p.ParamA).toBeCloseTo(0.8)
    expect(p.ParamMouthOpenY).toBeUndefined()
    expect(p.ParamEyeLOpen).toBe(0)
    expect(idsFromGroups(undefined).lipSync).toEqual(['ParamMouthOpenY'])
    expect(idsFromGroups([{ Name: 'LipSync', Ids: [] }]).lipSync).toEqual(['ParamMouthOpenY'])
  })

  it('表情放大：行为系统给 0.5 的开心，Live2D 上已经是明显的笑', () => {
    const p = live2dParams(pose(), { ...emptyExpressions(), happy: 0.5 }, { x: 0, y: 0 })
    expect(p.ParamEyeLSmile).toBeCloseTo(0.8)
    expect(p.ParamMouthUp).toBeCloseTo(0.8)
    const sad = live2dParams(pose(), { ...emptyExpressions(), sad: 0.5 }, { x: 0, y: 0 })
    expect(sad.ParamBrowLAngle).toBeLessThan(0)
    expect(sad.ParamMouthDown).toBeCloseTo(0.8)
  })

  it('挑动作：不用待机组、不用带特效的，轮着挑', () => {
    const motions = {
      Idle: [{ File: 'motions/mtn_01.motion3.json' }],
      '': [{ File: 'motions/mtn_02.motion3.json' }, { File: 'motions/mtn_03.motion3.json' }, { File: 'motions/special_01.motion3.json' }]
    }
    expect(pickMotion(motions, 0)).toEqual({ group: '', index: 0 })
    expect(pickMotion(motions, 1)).toEqual({ group: '', index: 1 })
    expect(pickMotion(motions, 2)).toEqual({ group: '', index: 0 })
    expect(pickMotion({ Idle: [{ File: 'a' }] }, 0)).toBeNull()
    expect(pickMotion(undefined, 0)).toBeNull()
  })
})
