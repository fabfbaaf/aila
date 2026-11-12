import type { Object3D, Vector3 } from 'three'

/** 用到的表情，名字和 VRM 1.0 的预设表情一致（aa = 张嘴说「啊」的口型） */
export type ExpressionName = 'happy' | 'sad' | 'surprised' | 'relaxed' | 'blink' | 'aa'

export type Expressions = Record<ExpressionName, number>

export function emptyExpressions(): Expressions {
  return { happy: 0, sad: 0, surprised: 0, relaxed: 0, blink: 0, aa: 0 }
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
