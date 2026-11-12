import { Object3D, Vector3 } from 'three'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'
import { VRMLoaderPlugin, VRMUtils, type VRM, type VRMHumanBoneName } from '@pixiv/three-vrm'
import type { Avatar, ExpressionName, Expressions, PoseState } from './types'

const clamp = (v: number, lo: number, hi: number): number => Math.min(hi, Math.max(lo, v))

const EXPRESSIONS: ExpressionName[] = ['happy', 'sad', 'surprised', 'relaxed', 'blink', 'aa']

/** 从 VRoid Studio 等工具导出的 .vrm 文件加载角色 */
export async function loadVrmAvatar(data: ArrayBuffer): Promise<VrmAvatar> {
  const loader = new GLTFLoader()
  loader.register((parser) => new VRMLoaderPlugin(parser))
  const gltf = await loader.parseAsync(data, '')
  const vrm = gltf.userData.vrm as VRM | undefined
  if (!vrm) throw new Error('这个文件不是 VRM 模型')

  VRMUtils.removeUnnecessaryVertices(gltf.scene)
  VRMUtils.rotateVRM0(vrm) // 旧版 VRM 0.x 模型朝向相反，统一转成面向镜头
  vrm.scene.traverse((o) => (o.frustumCulled = false))
  return new VrmAvatar(vrm)
}

export class VrmAvatar implements Avatar {
  readonly root: Object3D
  private lookObject = new Object3D()
  private tmp = new Vector3()
  /**
   * VRM 0.x 和 1.0 的骨骼坐标系绕 Y 轴差 180°，
   * 绕 X、Z 轴的旋转方向要反过来。
   */
  private flip: number
  /** 我们的表情名 → 模型里实际的表情名（有的模型大小写不同，比如 Surprised） */
  private expressionNames = new Map<ExpressionName, string>()

  constructor(private vrm: VRM) {
    this.root = vrm.scene
    this.flip = vrm.meta?.metaVersion === '0' ? -1 : 1
    if (vrm.lookAt) vrm.lookAt.target = this.lookObject

    const available = vrm.expressionManager?.expressions.map((e) => e.expressionName) ?? []
    for (const name of EXPRESSIONS) {
      const match = available.find((n) => n === name) ?? available.find((n) => n.toLowerCase() === name)
      if (match) this.expressionNames.set(name, match)
    }
  }

  update(dt: number, pose: PoseState, expr: Expressions): void {
    const f = this.flip

    // 手臂从 T 字姿势放下来（待验证：不同模型可能要微调角度）
    this.rotate('leftUpperArm', 0, 0, -1.2 * f)
    this.rotate('rightUpperArm', 0, 0, 1.2 * f)
    this.rotate('leftLowerArm', 0, 0, -0.15 * f)
    this.rotate('rightLowerArm', 0, 0, 0.15 * f)

    // 呼吸、后仰、摇晃
    this.rotate('spine', -pose.bodyLean * f, 0, pose.bodySway * f)
    this.rotate('chest', -0.03 * pose.breath * f, 0, 0)
    this.rotate('leftShoulder', 0, 0, 0.02 * pose.breath * f)
    this.rotate('rightShoulder', 0, 0, -0.02 * pose.breath * f)

    // 头跟着视线转，眼睛由 VRM 自带的 lookAt 处理
    const head = this.getHeadWorldPosition(this.tmp)
    const dx = pose.lookTarget.x - head.x
    const dy = pose.lookTarget.y - head.y
    const dz = pose.lookTarget.z - head.z
    const yaw = clamp(Math.atan2(dx, dz), -0.7, 0.7)
    const pitch = clamp(-Math.atan2(dy, Math.hypot(dx, dz)), -0.4, 0.4)
    const headYaw = yaw * 0.4 + pose.headYaw * 0.5
    this.rotate('neck', (pitch * 0.35 + pose.headPitch * 0.5) * f, headYaw, pose.headRoll * 0.5 * f)
    this.rotate('head', (pitch * 0.35 + pose.headPitch * 0.5) * f, headYaw, pose.headRoll * 0.5 * f)

    this.lookObject.position.copy(pose.lookTarget)
    this.lookObject.updateMatrixWorld()

    const em = this.vrm.expressionManager
    if (em) for (const [name, actual] of this.expressionNames) em.setValue(actual, clamp(expr[name], 0, 1))

    this.vrm.update(dt)
  }

  getNeckWorldPosition(target: Vector3): Vector3 {
    const neck = this.vrm.humanoid.getRawBoneNode('neck')
    return neck ? neck.getWorldPosition(target) : target.set(0, 1.3, 0)
  }

  getHeadWorldPosition(target: Vector3): Vector3 {
    const head = this.vrm.humanoid.getRawBoneNode('head')
    return head ? head.getWorldPosition(target).add(this.tmp2.set(0, 0.08, 0)) : target.set(0, 1.4, 0)
  }

  dispose(): void {
    VRMUtils.deepDispose(this.vrm.scene)
  }

  private tmp2 = new Vector3()

  private rotate(bone: VRMHumanBoneName, x: number, y: number, z: number): void {
    this.vrm.humanoid.getNormalizedBoneNode(bone)?.rotation.set(x, y, z)
  }
}
