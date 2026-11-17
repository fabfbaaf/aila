import { Object3D, Vector3 } from 'three'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'
import { VRMLoaderPlugin, VRMUtils, type VRM, type VRMHumanBoneName } from '@pixiv/three-vrm'
import { VOWELS, type Avatar, type ExpressionName, type Expressions, type PoseState } from './types'

const clamp = (v: number, lo: number, hi: number): number => Math.min(hi, Math.max(lo, v))

// VRM 0.x 的 A/I/U/E/O 口型，three-vrm 加载时会映射成 aa/ih/ou/ee/oh
const EXPRESSIONS: ExpressionName[] = ['happy', 'sad', 'surprised', 'relaxed', 'blink', ...VOWELS]

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

const mix = (a: number, b: number, t: number): number => a + (b - a) * t

type Rot = { x: number; y: number; z: number }

/**
 * 手臂的角度（VRM 1.0 的标准骨骼坐标，0.x 的模型在外面乘 flip）。
 * 骨骼的旋转顺序是先 z 再 y 再 x（three.js 的 XYZ）：
 * - 大臂：z 从 T 字往下放（左手负、右手正是放下）；放下以后 x 往身前抬（负数朝前）
 * - 小臂：沿着手臂的方向是 x（绕它转只是拧手腕，不弯手肘）；手肘往前弯是 y（右手正、左手负），往上弯是 z
 * 几个动作按权重叠：放下的姿势 → 敲键盘 → 挥手（只动右手）→ 伸懒腰（两只手）。
 */
export function armAngles(p: Pick<PoseState, 'armRaise' | 'forearmSwing' | 'armsUp' | 'typing' | 'typingBob'>): {
  leftUpper: Rot
  rightUpper: Rot
  leftLower: Rot
  rightLower: Rot
} {
  const bob = p.typingBob * 0.08 * p.typing
  // 放下 → 敲键盘：大臂贴着身体稍微朝前，小臂往前、往中间收，两只手在肚子前面。
  // 角度是在示例模型上量出来的：手在手肘下面一点、身前约 23 厘米、离中线约 9 厘米
  let lu: Rot = { x: mix(0, -0.2, p.typing), y: 0, z: mix(-1.2, -1.3, p.typing) }
  let ru: Rot = { x: mix(0, -0.2, p.typing), y: 0, z: mix(1.2, 1.3, p.typing) }
  let ll: Rot = { x: 0, y: mix(0, -1.0, p.typing) + bob, z: mix(-0.15, -0.5, p.typing) }
  let rl: Rot = { x: 0, y: mix(0, 1.0, p.typing) - bob, z: mix(0.15, 0.5, p.typing) }
  // 挥手：右大臂抬到肩膀高度附近，小臂往上弯，左右摆
  ru = { x: mix(ru.x, -0.2, p.armRaise), y: ru.y, z: mix(ru.z, 0.2, p.armRaise) }
  rl = { x: rl.x, y: mix(rl.y, 0, p.armRaise), z: mix(rl.z, -1.5 + p.forearmSwing * 0.35, p.armRaise) }
  // 伸懒腰：两只手举过头，小臂稍微弯
  lu = { x: mix(lu.x, -0.15, p.armsUp), y: lu.y, z: mix(lu.z, 1.25, p.armsUp) }
  ru = { x: mix(ru.x, -0.15, p.armsUp), y: ru.y, z: mix(ru.z, -1.25, p.armsUp) }
  ll = { x: ll.x, y: mix(ll.y, 0, p.armsUp), z: mix(ll.z, 0.35, p.armsUp) }
  rl = { x: rl.x, y: mix(rl.y, 0, p.armsUp), z: mix(rl.z, -0.35, p.armsUp) }
  return { leftUpper: lu, rightUpper: ru, leftLower: ll, rightLower: rl }
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

    // 手臂从 T 字姿势放下来（待验证：不同模型可能要微调角度），再叠上挥手、伸懒腰、敲键盘
    const arms = armAngles(pose)
    this.rotate('leftUpperArm', arms.leftUpper.x * f, arms.leftUpper.y, arms.leftUpper.z * f)
    this.rotate('rightUpperArm', arms.rightUpper.x * f, arms.rightUpper.y, arms.rightUpper.z * f)
    this.rotate('leftLowerArm', arms.leftLower.x * f, arms.leftLower.y, arms.leftLower.z * f)
    this.rotate('rightLowerArm', arms.rightLower.x * f, arms.rightLower.y, arms.rightLower.z * f)

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
