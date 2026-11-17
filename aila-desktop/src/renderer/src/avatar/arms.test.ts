import { describe, expect, it } from 'vitest'
import { Behavior } from '../behavior/Behavior'
import { armAngles } from './VrmAvatar'

const rest = { armRaise: 0, forearmSwing: 0, armsUp: 0, typing: 0, typingBob: 0 }

describe('手臂动作', () => {
  it('什么都不做时和以前一样：从 T 字放下来', () => {
    const a = armAngles(rest)
    expect(a.leftUpper).toEqual({ x: 0, y: 0, z: -1.2 })
    expect(a.rightUpper).toEqual({ x: 0, y: 0, z: 1.2 })
    expect(a.leftLower).toEqual({ x: 0, y: 0, z: -0.15 })
    expect(a.rightLower).toEqual({ x: 0, y: 0, z: 0.15 })
  })

  it('挥手只动右手；小臂跟着左右摆', () => {
    const a = armAngles({ ...rest, armRaise: 1, forearmSwing: 1 })
    const b = armAngles({ ...rest, armRaise: 1, forearmSwing: -1 })
    expect(a.leftUpper).toEqual(armAngles(rest).leftUpper)
    expect(a.rightUpper.z).toBeLessThan(0.5) // 抬起来了
    expect(a.rightLower.z).not.toBe(b.rightLower.z)
  })

  it('伸懒腰两只手都举过头；敲键盘两只手朝前', () => {
    const up = armAngles({ ...rest, armsUp: 1 })
    expect(up.leftUpper.z).toBeGreaterThan(1)
    expect(up.rightUpper.z).toBeLessThan(-1)
    // 敲键盘：大臂朝前一点，手肘往前弯（小臂的 y，左右相反）；不拧手腕（小臂的 x 不动）
    const typing = armAngles({ ...rest, typing: 1 })
    expect(typing.leftUpper.x).toBeLessThan(-0.1)
    expect(typing.rightLower.y).toBeGreaterThan(0.8)
    expect(typing.leftLower.y).toBeLessThan(-0.8)
    expect(typing.rightLower.x).toBe(0)
  })
})

describe('行为：动作', () => {
  const run = (b: Behavior, seconds: number): void => {
    for (let t = 0; t < seconds; t += 1 / 60) b.update(1 / 60)
  }

  it('挥手时右手抬起来，挥完放下', () => {
    const b = new Behavior()
    b.gesture('wave')
    run(b, 0.6)
    expect(b.pose.armRaise).toBeGreaterThan(0.8)
    run(b, 2.5)
    expect(b.pose.armRaise).toBeLessThan(0.05)
  })

  it('做编程任务时敲键盘，挥手时手让开，做完放下', () => {
    const b = new Behavior()
    b.setWorking(true)
    run(b, 1.5)
    expect(b.pose.typing).toBeGreaterThan(0.9)
    b.gesture('stretch')
    run(b, 0.8)
    expect(b.pose.armsUp).toBeGreaterThan(0.8)
    expect(b.pose.typing).toBeLessThan(0.3)
    b.setWorking(false)
    run(b, 3)
    expect(b.pose.typing).toBeLessThan(0.05)
    expect(b.pose.armsUp).toBeLessThan(0.05)
  })
})
