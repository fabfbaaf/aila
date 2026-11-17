import type { Object3D, Vector3 } from 'three'

/** 五个元音口型，名字和 VRM 1.0 一致：啊、衣、乌、诶、哦 */
export type Vowel = 'aa' | 'ih' | 'ou' | 'ee' | 'oh'
export const VOWELS: readonly Vowel[] = ['aa', 'ih', 'ou', 'ee', 'oh']
export type Mouth = Record<Vowel, number>

/** 用到的表情，名字和 VRM 1.0 的预设表情一致 */
export type ExpressionName = 'happy' | 'sad' | 'surprised' | 'relaxed' | 'blink' | Vowel

export type Expressions = Record<ExpressionName, number>

export function emptyExpressions(): Expressions {
  return { happy: 0, sad: 0, surprised: 0, relaxed: 0, blink: 0, aa: 0, ih: 0, ou: 0, ee: 0, oh: 0 }
}

export function closedMouth(): Mouth {
  return { aa: 0, ih: 0, ou: 0, ee: 0, oh: 0 }
}

/** 行为系统每帧算出的姿态，角色只负责把它画出来 */
export interface PoseState {
  /** 视线目标（世界坐标） */
  lookTarget: Vector3
  /** 正数 = 低头 */
  headPitch: number
  /** 在视线方向之外额外转头（摇头用），正数 = 转向角色的左边 */
  headYaw: number
  /** 正数 = 歪头 */
  headRoll: number
  /** 正数 = 身体后仰 */
  bodyLean: number
  /** 左右摇晃（被拖动时） */
  bodySway: number
  /** 呼吸，-1 到 1 */
  breath: number
  /** 右手抬起来（挥手），0 到 1 */
  armRaise: number
  /** 挥手时小臂左右摆，-1 到 1 */
  forearmSwing: number
  /** 两只手举过头（伸懒腰），0 到 1 */
  armsUp: number
  /** 双手在身前敲键盘（她在做编程任务时），0 到 1 */
  typing: number
  /** 敲键盘时手的上下起伏，-1 到 1 */
  typingBob: number
}

/**
 * 角色的统一接口。
 * 现在有两种实现：没有模型时的占位角色、加载 VRM 后的正式角色。
 */
export interface Avatar {
  readonly root: Object3D
  update(dt: number, pose: PoseState, expr: Expressions): void
  /** 脖子位置，用来判断点在头上还是身上 */
  getNeckWorldPosition(target: Vector3): Vector3
  /** 头部中心，用来算视线方向和镜头构图 */
  getHeadWorldPosition(target: Vector3): Vector3
  dispose(): void
}
