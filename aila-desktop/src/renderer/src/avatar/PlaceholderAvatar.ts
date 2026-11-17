import {
  CapsuleGeometry,
  CircleGeometry,
  ConeGeometry,
  CylinderGeometry,
  Group,
  Mesh,
  MeshBasicMaterial,
  MeshToonMaterial,
  SphereGeometry,
  Vector3,
  type Material
} from 'three'
import type { Avatar, Expressions, PoseState } from './types'

const clamp = (v: number, lo: number, hi: number): number => Math.min(hi, Math.max(lo, v))

/**
 * 没有 VRM 模型时用的简易角色：用基本几何体拼出来，
 * 但和正式角色走同一套接口，行为系统可以先在它身上调好。
 */
export class PlaceholderAvatar implements Avatar {
  readonly root = new Group()
  private body = new Group()
  private neck = new Group()
  private torso: Mesh
  private eyes: Mesh[] = []
  private blush: Mesh[] = []
  private mouth: Mesh
  private arms: Mesh[] = []
  private materials: Material[] = []
  private tmp = new Vector3()

  constructor() {
    const skin = this.toon('#ffe6d8')
    const hair = this.toon('#d8d3e6')
    const dress = this.toon('#f4f4f8')
    const ribbon = this.toon('#8fa9d6')
    const shoe = this.toon('#6b6f86')
    const eye = this.toon('#56628f')
    const mouthMat = this.toon('#c97a86')
    const blushMat = new MeshBasicMaterial({ color: '#ff9fb0', transparent: true, opacity: 0 })
    this.materials.push(blushMat)

    // 腿和鞋
    for (const x of [-0.07, 0.07]) {
      const leg = new Mesh(new CylinderGeometry(0.035, 0.035, 0.42, 12), skin)
      leg.position.set(x, 0.23, 0)
      const foot = new Mesh(new SphereGeometry(0.05, 12, 8), shoe)
      foot.scale.set(1, 0.6, 1.4)
      foot.position.set(x, 0.03, 0.02)
      this.root.add(leg, foot)
    }

    // 身体（以腰部为支点倾斜）
    this.body.position.y = 0.55
    this.root.add(this.body)

    const skirt = new Mesh(new ConeGeometry(0.27, 0.36, 28, 1, true), dress)
    skirt.position.y = 0.06
    this.torso = new Mesh(new CapsuleGeometry(0.13, 0.3, 8, 16), dress)
    this.torso.position.y = 0.36
    const bow = new Mesh(new SphereGeometry(0.035, 12, 8), ribbon)
    bow.scale.set(1.8, 1, 0.8)
    bow.position.set(0, 0.55, 0.12)
    this.body.add(skirt, this.torso, bow)

    for (const side of [-1, 1]) {
      const arm = new Mesh(new CapsuleGeometry(0.04, 0.34, 6, 10), skin)
      arm.geometry.translate(0, -0.2, 0) // 让旋转支点在肩膀
      arm.position.set(side * 0.17, 0.58, 0)
      arm.rotation.z = side * 0.12
      this.arms.push(arm)
      this.body.add(arm)
    }

    // 头（以脖子为支点转动）
    this.neck.position.y = 0.64
    this.body.add(this.neck)

    const head = new Mesh(new SphereGeometry(0.2, 32, 24), skin)
    head.position.y = 0.19
    const hairCap = new Mesh(new SphereGeometry(0.218, 32, 16, 0, Math.PI * 2, 0, Math.PI * 0.56), hair)
    hairCap.position.set(0, 0.2, -0.012)
    hairCap.rotation.x = -0.42
    const backHair = new Mesh(new CapsuleGeometry(0.17, 0.32, 8, 16), hair)
    backHair.position.set(0, 0.06, -0.09)
    this.neck.add(head, hairCap, backHair)

    for (const side of [-1, 1]) {
      const e = new Mesh(new SphereGeometry(0.026, 16, 12), eye)
      e.position.set(side * 0.07, 0.19, 0.18)
      this.eyes.push(e)
      const b = new Mesh(new CircleGeometry(0.032, 20), blushMat)
      b.position.set(side * 0.112, 0.13, 0.172)
      b.rotation.y = side * 0.5
      this.blush.push(b)
      this.neck.add(e, b)
    }

    this.mouth = new Mesh(new SphereGeometry(1, 16, 8), mouthMat)
    this.mouth.position.set(0, 0.1, 0.192)
    this.neck.add(this.mouth)
  }

  update(_dt: number, pose: PoseState, expr: Expressions): void {
    // 呼吸
    this.torso.scale.set(1 + pose.breath * 0.012, 1 + pose.breath * 0.015, 1)

    // 身体
    this.body.rotation.x = -pose.bodyLean
    this.body.rotation.z = pose.bodySway
    // arms[0] 是她的右手（画面左边）：往外抬是 z 变小；伸懒腰两只手都举起来；敲键盘时手往前伸
    this.arms[0].rotation.z = -0.12 - pose.bodySway * 1.5 - pose.armRaise * (2.3 + pose.forearmSwing * 0.25) - pose.armsUp * 2.6
    this.arms[1].rotation.z = 0.12 - pose.bodySway * 1.5 + pose.armsUp * 2.6
    for (const arm of this.arms) arm.rotation.x = -pose.typing * (0.9 + pose.typingBob * 0.06)

    // 头跟着视线转
    const head = this.getHeadWorldPosition(this.tmp)
    const dx = pose.lookTarget.x - head.x
    const dy = pose.lookTarget.y - head.y
    const dz = pose.lookTarget.z - head.z
    const yaw = clamp(Math.atan2(dx, dz), -0.7, 0.7)
    const pitch = clamp(-Math.atan2(dy, Math.hypot(dx, dz)), -0.4, 0.4)
    this.neck.rotation.set(pitch * 0.7 + pose.headPitch, yaw * 0.8 + pose.headYaw, pose.headRoll - yaw * 0.08)

    // 表情
    const open = (1 - expr.blink) * (1 - 0.6 * expr.happy) * (1 - 0.35 * expr.relaxed) * (1 + 0.35 * expr.surprised)
    for (const [i, e] of this.eyes.entries()) {
      e.scale.set(1 + 0.15 * expr.surprised, clamp(open, 0.08, 1.4), 1)
      e.position.x = (i === 0 ? -0.07 : 0.07) + yaw * 0.012
    }
    const blush = clamp(expr.relaxed * 0.8 + expr.happy * 0.3, 0, 0.85)
    for (const b of this.blush) (b.material as MeshBasicMaterial).opacity = blush
    // 口型：张开程度看 aa/oh，嘴角宽窄看 ih/ee（咧开）和 ou/oh（嘟起）
    const opening = expr.aa + 0.7 * expr.oh + 0.35 * expr.ee + 0.25 * expr.ih + 0.3 * expr.ou
    const width = 1 + 0.5 * expr.happy - 0.35 * expr.surprised - 0.25 * expr.sad + 0.3 * (expr.ih + expr.ee) - 0.4 * (expr.ou + expr.oh)
    this.mouth.scale.set(0.028 * width, 0.006 + 0.016 * expr.surprised + 0.02 * opening, 0.01)
  }

  getNeckWorldPosition(target: Vector3): Vector3 {
    return this.neck.getWorldPosition(target)
  }

  getHeadWorldPosition(target: Vector3): Vector3 {
    return this.neck.localToWorld(target.set(0, 0.19, 0))
  }

  dispose(): void {
    this.root.traverse((o) => {
      if (o instanceof Mesh) o.geometry.dispose()
    })
    for (const m of this.materials) m.dispose()
  }

  private toon(color: string): MeshToonMaterial {
    const m = new MeshToonMaterial({ color })
    this.materials.push(m)
    return m
  }
}
