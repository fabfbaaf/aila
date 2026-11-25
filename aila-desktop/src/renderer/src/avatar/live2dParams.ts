/**
 * 行为系统算出的姿态、表情 → Live2D 模型的参数（Cubism 的标准参数名）。
 *
 * 为什么用标准参数：Live2D 官方编辑器建模时默认就是这一套（ParamAngleX、ParamEyeLOpen、ParamMouthOpenY……），
 * 大部分模型都有；模型没有的参数设了也没关系（Cubism 会忽略）。
 * - 张嘴、眨眼用模型自己在 model3.json 里声明的参数（Groups 里的 LipSync、EyeBlink）：有的模型张嘴不叫 ParamMouthOpenY
 *   （比如官方示例的真央是 ParamA），按标准名写就张不开嘴
 * - 表情比 VRM 放大一些（EXPRESS 倍）：行为系统的表情本来就收着，Live2D 的平面脸上同样的幅度几乎看不出来
 * - 常见的可选参数（嘴角上扬 / 下垂、眉毛角度、脸红）也写上，模型有就用
 * - 手臂没有统一的参数（名字、方向各家不一样），挥手、伸懒腰交给模型自带的动作（Live2DAvatar.gesture）
 * 纯函数，方便单测。
 */
import type { Expressions, PoseState } from './types'

export type Live2DParams = Record<string, number>

/** 模型自己声明的张嘴、眨眼参数（model3.json 的 Groups）；没写就用标准名 */
export interface Live2DIds {
  lipSync: string[]
  eyeBlink: string[]
}

export const STANDARD_IDS: Live2DIds = { lipSync: ['ParamMouthOpenY'], eyeBlink: ['ParamEyeLOpen', 'ParamEyeROpen'] }

/** 表情放大多少倍（再封顶到 1） */
export const EXPRESS = 1.6

/** model3.json 的 Groups → 张嘴、眨眼参数（Name 是 LipSync / EyeBlink，Target 是 Parameter） */
export function idsFromGroups(groups: { Target?: string; Name?: string; Ids?: string[] }[] | undefined): Live2DIds {
  const find = (name: string): string[] | undefined => {
    const g = groups?.find((x) => x.Name === name && (x.Target ?? 'Parameter') === 'Parameter' && x.Ids?.length)
    return g?.Ids?.filter((id) => typeof id === 'string' && id.length > 0 && id.length < 100)
  }
  return { lipSync: find('LipSync') ?? STANDARD_IDS.lipSync, eyeBlink: find('EyeBlink') ?? STANDARD_IDS.eyeBlink }
}

const clamp = (v: number, lo: number, hi: number): number => Math.min(hi, Math.max(lo, v))

/**
 * look：视线方向，-1 到 1，按屏幕方向算：x 正 = 往屏幕右边看，y 正 = 往上看
 * （和 Cubism 的约定一致：ParamAngleX、ParamEyeBallX 正数都是朝屏幕右边）
 */
export function live2dParams(pose: PoseState, expr: Expressions, look: { x: number; y: number }, ids: Live2DIds = STANDARD_IDS): Live2DParams {
  const lx = clamp(look.x, -1, 1)
  const ly = clamp(look.y, -1, 1)
  // 头：视线带一部分，再叠上动作（摇头、低头、歪头）。VRM 那边的弧度换成 Live2D 的角度（±30）
  const angleX = clamp(lx * 18 + (pose.headYaw / 0.8) * 30, -30, 30)
  const angleY = clamp(ly * 15 - (pose.headPitch / 0.6) * 30, -30, 30)
  const angleZ = clamp((pose.headRoll / 0.5) * 30, -30, 30)
  // 身体：被拖动时的摇晃、后仰，跟着头转一点
  const bodyX = clamp(angleX * 0.3 + (pose.bodySway / 0.3) * 10, -10, 10)
  const bodyY = clamp((pose.bodyLean / 0.3) * 10, -10, 10)

  const up = (v: number): number => clamp(v * EXPRESS, 0, 1)
  const happy = up(expr.happy)
  const sad = up(expr.sad)
  const surprised = up(expr.surprised)
  const relaxed = up(expr.relaxed)
  // 眼睛：眨眼闭上，笑和放松时眯一点，惊讶时睁大（标准参数最大 1，有的模型到 2）
  const open = clamp((1 - clamp(expr.blink, 0, 1)) * (1 - 0.4 * happy - 0.3 * relaxed) + 0.3 * surprised, 0, 1.2)
  // 嘴：五个元音合成张嘴大小和嘴形（-1 撇嘴 … 1 咧嘴笑）
  const mouthOpen = clamp(Math.max(expr.aa, expr.oh * 0.85, expr.ou * 0.55, expr.ee * 0.5, expr.ih * 0.35) + 0.3 * surprised, 0, 1)
  const mouthForm = clamp(0.8 * happy - 0.85 * sad + 0.6 * (expr.ih + expr.ee) - 0.5 * expr.ou, -1, 1)
  const brow = clamp(0.7 * surprised - 0.5 * sad, -1, 1)

  const out: Live2DParams = {
    ParamAngleX: angleX,
    ParamAngleY: angleY,
    ParamAngleZ: angleZ,
    ParamBodyAngleX: bodyX,
    ParamBodyAngleY: bodyY,
    ParamBodyAngleZ: clamp(angleZ * 0.3, -10, 10),
    ParamEyeBallX: lx,
    ParamEyeBallY: ly,
    ParamEyeLSmile: happy,
    ParamEyeRSmile: happy,
    ParamBrowLY: brow,
    ParamBrowRY: brow,
    // 难过时眉头往下皱（眉毛角度），有的模型有
    ParamBrowLAngle: clamp(-0.7 * sad + 0.3 * surprised, -1, 1),
    ParamBrowRAngle: clamp(-0.7 * sad + 0.3 * surprised, -1, 1),
    ParamMouthForm: mouthForm,
    // 有的模型把嘴角拆成两个参数（上扬 / 下垂）
    ParamMouthUp: happy,
    ParamMouthDown: sad,
    ParamCheek: clamp(happy * 0.8 + relaxed * 0.3, 0, 1),
    // 呼吸：行为系统给的是 -1..1，标准参数是 0..1
    ParamBreath: clamp((pose.breath + 1) / 2, 0, 1)
  }
  for (const id of ids.eyeBlink) out[id] = open
  for (const id of ids.lipSync) out[id] = mouthOpen
  return out
}

/**
 * 挥手、伸懒腰、开心时播模型自带的哪个动作（手臂交给画师做好的动作，不去猜各家的手臂参数）：
 * 不用待机组（Idle）；名字带 special / effect 的特殊动作不用（官方示例里这类带特效，不能和一般表情一起用）；
 * 同一个手势轮着挑，每次不一样。没有可用的就返回 null（只做头和表情）
 */
export function pickMotion(motions: Record<string, { File?: string }[]> | undefined, turn: number): { group: string; index: number } | null {
  const usable: { group: string; index: number }[] = []
  for (const [group, list] of Object.entries(motions ?? {})) {
    if (/idle/i.test(group)) continue
    list.forEach((m, index) => {
      if (!/special|effect/i.test(m.File ?? '')) usable.push({ group, index })
    })
  }
  if (!usable.length) return null
  return usable[((turn % usable.length) + usable.length) % usable.length]
}

/** 视线目标（世界坐标）和头的位置 → look（-1..1）；dz 是目标在头前面多远 */
export function lookFrom(dx: number, dy: number, dz: number): { x: number; y: number } {
  const z = Math.max(0.3, dz)
  return { x: clamp(Math.atan2(dx, z) / 0.7, -1, 1), y: clamp(Math.atan2(dy, Math.hypot(dx, z)) / 0.5, -1, 1) }
}
