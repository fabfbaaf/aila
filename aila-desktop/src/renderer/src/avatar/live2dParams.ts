/**
 * 行为系统算出的姿态、表情 → Live2D 模型的参数（Cubism 的标准参数名）。
 *
 * 为什么用标准参数：Live2D 官方编辑器建模时默认就是这一套（ParamAngleX、ParamEyeLOpen、ParamMouthOpenY……），
 * 大部分模型都有；模型没有的参数设了也没关系（Cubism 会忽略）。手臂没有统一的参数，挥手、伸懒腰在 Live2D 上
 * 只表现成头和身体的动作和表情。纯函数，方便单测。
 */
import type { Expressions, PoseState } from './types'

export type Live2DParams = Record<string, number>

const clamp = (v: number, lo: number, hi: number): number => Math.min(hi, Math.max(lo, v))

/**
 * look：视线方向，-1 到 1，按屏幕方向算：x 正 = 往屏幕右边看，y 正 = 往上看
 * （和 Cubism 的约定一致：ParamAngleX、ParamEyeBallX 正数都是朝屏幕右边）
 */
export function live2dParams(pose: PoseState, expr: Expressions, look: { x: number; y: number }): Live2DParams {
  const lx = clamp(look.x, -1, 1)
  const ly = clamp(look.y, -1, 1)
  // 头：视线带一部分，再叠上动作（摇头、低头、歪头）。VRM 那边的弧度换成 Live2D 的角度（±30）
  const angleX = clamp(lx * 18 + (pose.headYaw / 0.8) * 30, -30, 30)
  const angleY = clamp(ly * 15 - (pose.headPitch / 0.6) * 30, -30, 30)
  const angleZ = clamp((pose.headRoll / 0.5) * 30, -30, 30)
  // 身体：被拖动时的摇晃、后仰，跟着头转一点
  const bodyX = clamp(angleX * 0.3 + (pose.bodySway / 0.3) * 10, -10, 10)
  const bodyY = clamp((pose.bodyLean / 0.3) * 10, -10, 10)

  const happy = clamp(expr.happy, 0, 1)
  const sad = clamp(expr.sad, 0, 1)
  const surprised = clamp(expr.surprised, 0, 1)
  const relaxed = clamp(expr.relaxed, 0, 1)
  // 眼睛：眨眼闭上，笑和放松时眯一点，惊讶时睁大（标准参数最大 1，有的模型到 2）
  const open = clamp((1 - clamp(expr.blink, 0, 1)) * (1 - 0.35 * happy - 0.3 * relaxed) + 0.25 * surprised, 0, 1.2)
  // 嘴：五个元音合成张嘴大小和嘴形（-1 撇嘴 … 1 咧嘴笑）
  const mouthOpen = clamp(Math.max(expr.aa, expr.oh * 0.85, expr.ou * 0.55, expr.ee * 0.5, expr.ih * 0.35) + 0.3 * surprised, 0, 1)
  const mouthForm = clamp(0.7 * happy - 0.8 * sad + 0.6 * (expr.ih + expr.ee) - 0.5 * expr.ou, -1, 1)

  return {
    ParamAngleX: angleX,
    ParamAngleY: angleY,
    ParamAngleZ: angleZ,
    ParamBodyAngleX: bodyX,
    ParamBodyAngleY: bodyY,
    ParamBodyAngleZ: clamp(angleZ * 0.3, -10, 10),
    ParamEyeBallX: lx,
    ParamEyeBallY: ly,
    ParamEyeLOpen: open,
    ParamEyeROpen: open,
    ParamEyeLSmile: happy,
    ParamEyeRSmile: happy,
    ParamBrowLY: clamp(0.6 * surprised - 0.5 * sad, -1, 1),
    ParamBrowRY: clamp(0.6 * surprised - 0.5 * sad, -1, 1),
    ParamMouthOpenY: mouthOpen,
    ParamMouthForm: mouthForm,
    ParamCheek: clamp(happy * 0.6 + relaxed * 0.3, 0, 1),
    // 呼吸：行为系统给的是 -1..1，标准参数是 0..1
    ParamBreath: clamp((pose.breath + 1) / 2, 0, 1)
  }
}

/** 视线目标（世界坐标）和头的位置 → look（-1..1）；dz 是目标在头前面多远 */
export function lookFrom(dx: number, dy: number, dz: number): { x: number; y: number } {
  const z = Math.max(0.3, dz)
  return { x: clamp(Math.atan2(dx, z) / 0.7, -1, 1), y: clamp(Math.atan2(dy, Math.hypot(dx, z)) / 0.5, -1, 1) }
}
