import { readFileSync } from 'fs'
import { join } from 'path'
import { describe, expect, it } from 'vitest'

/** 读 .glb 里的 JSON（不解析几何，只看结构） */
function gltfJson(path: string): Record<string, any> {
  const buf = readFileSync(path)
  expect(buf.readUInt32LE(0)).toBe(0x46546c67) // glTF
  expect(buf.readUInt32LE(4)).toBe(2)
  const len = buf.readUInt32LE(12)
  expect(buf.readUInt32LE(16)).toBe(0x4e4f534a) // JSON
  return JSON.parse(buf.subarray(20, 20 + len).toString('utf8'))
}

const FILE = join(__dirname, '..', '..', 'public', 'avatar', 'aila.vrm')

describe('自带的原创形象', () => {
  const json = gltfJson(FILE)
  const vrm = json.extensions.VRMC_vrm

  it('是 VRM 1.0，必需的人形骨骼都在', () => {
    expect(vrm.specVersion).toBe('1.0')
    const required = [
      'hips', 'spine', 'head',
      'leftUpperLeg', 'leftLowerLeg', 'leftFoot', 'rightUpperLeg', 'rightLowerLeg', 'rightFoot',
      'leftUpperArm', 'leftLowerArm', 'leftHand', 'rightUpperArm', 'rightLowerArm', 'rightHand'
    ]
    for (const b of required) expect(vrm.humanoid.humanBones[b]?.node, b).toBeTypeOf('number')
    // 动作、视线要用的也在
    for (const b of ['chest', 'neck', 'leftShoulder', 'rightShoulder', 'leftEye', 'rightEye']) {
      expect(vrm.humanoid.humanBones[b]?.node, b).toBeTypeOf('number')
    }
  })

  it('她用到的表情都有，而且真的绑到了形变上', () => {
    for (const e of ['happy', 'sad', 'surprised', 'relaxed', 'blink', 'aa', 'ih', 'ou', 'ee', 'oh']) {
      const binds = vrm.expressions.preset[e]?.morphTargetBinds ?? []
      expect(binds.length, e).toBeGreaterThan(0)
      for (const b of binds) {
        const mesh = json.meshes[json.nodes[b.node].mesh]
        expect(mesh.primitives[0].targets.length).toBeGreaterThan(b.index)
      }
    }
  })

  it('原创、允许随项目一起分发和修改', () => {
    expect(vrm.meta.name).toBe('艾拉')
    expect(vrm.meta.allowRedistribution).toBe(true)
    expect(vrm.meta.modification).toBe('allowModificationRedistribution')
    expect(vrm.meta.copyrightInformation).toContain('make-avatar.mjs')
  })

  it('不大：三角形不到 5 万，文件不到 4MB', () => {
    const tris = json.meshes.reduce((n: number, m: any) => n + json.accessors[m.primitives[0].indices].count / 3, 0)
    expect(tris).toBeLessThan(50_000)
    expect(readFileSync(FILE).length).toBeLessThan(4 * 1024 * 1024)
  })
})
