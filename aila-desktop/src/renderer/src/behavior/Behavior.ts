import { Vector3 } from 'three'
import type { Emotion, Gesture } from '../../../shared/chat'
import { emptyExpressions, VOWELS, type Expressions, type Mouth, type PoseState } from '../avatar/types'
import { greeting, lines, pick } from '../persona/lines'

type Offsets = Pick<PoseState, 'headPitch' | 'headYaw' | 'headRoll' | 'bodyLean' | 'bodySway' | 'armRaise' | 'forearmSwing' | 'armsUp'>

/** 一个反应 = 几段按顺序播放的表情和姿态 */
interface Phase {
  dur: number
  expr?: Partial<Expressions>
  pose?: Partial<Offsets>
}

export interface BehaviorConfig {
  /** 鼠标多久不动后开始东张西望（秒） */
  glanceAfter: number
  /** 鼠标多久不动后开始犯困（秒） */
  sleepAfter: number
}

const DEFAULT_CONFIG: BehaviorConfig = { glanceAfter: 8, sleepAfter: 300 }

/** 指数平滑，和帧率无关 */
const approach = (cur: number, target: number, rate: number, dt: number): number =>
  cur + (target - cur) * (1 - Math.exp(-rate * dt))

const clamp = (v: number, lo: number, hi: number): number => Math.min(hi, Math.max(lo, v))

/** 大模型回复里的表情标签 → 持续的表情（幅度都偏小，符合她内敛的性格） */
const MOODS: Record<Exclude<Emotion, 'neutral'>, Partial<Expressions>> = {
  happy: { happy: 0.55 },
  sad: { sad: 0.55 },
  surprised: { surprised: 0.6 },
  relaxed: { relaxed: 0.55 }
}

/** 动作标签 → 一小段姿态 */
const GESTURES: Record<Gesture, Phase[]> = {
  nod: [
    { dur: 0.16, pose: { headPitch: 0.3 } },
    { dur: 0.16, pose: { headPitch: -0.02 } },
    { dur: 0.16, pose: { headPitch: 0.24 } },
    { dur: 0.3, pose: { headPitch: 0 } }
  ],
  shake: [
    { dur: 0.14, pose: { headYaw: 0.32 } },
    { dur: 0.14, pose: { headYaw: -0.32 } },
    { dur: 0.14, pose: { headYaw: 0.24 } },
    { dur: 0.14, pose: { headYaw: -0.16 } },
    { dur: 0.25, pose: { headYaw: 0 } }
  ],
  tilt: [{ dur: 1.6, pose: { headRoll: 0.24, headPitch: 0.04 } }],
  think: [{ dur: 1.8, expr: { relaxed: 0.2 }, pose: { headRoll: -0.15, headPitch: -0.1 } }],
  bow: [
    { dur: 0.5, pose: { headPitch: 0.35, bodyLean: -0.16 } },
    { dur: 0.5, pose: { headPitch: 0.12, bodyLean: -0.05 } }
  ],
  // 抬右手，小臂左右摆两下，放下
  wave: [
    { dur: 0.3, expr: { happy: 0.35 }, pose: { armRaise: 1, headRoll: 0.08 } },
    { dur: 0.22, expr: { happy: 0.35 }, pose: { armRaise: 1, forearmSwing: 1, headRoll: 0.08 } },
    { dur: 0.22, expr: { happy: 0.35 }, pose: { armRaise: 1, forearmSwing: -1, headRoll: 0.08 } },
    { dur: 0.22, expr: { happy: 0.35 }, pose: { armRaise: 1, forearmSwing: 1, headRoll: 0.08 } },
    { dur: 0.22, expr: { happy: 0.35 }, pose: { armRaise: 1, forearmSwing: -1, headRoll: 0.08 } },
    { dur: 0.4, expr: { happy: 0.25 } }
  ],
  // 两手举过头、仰一下头、眯眼，再慢慢放下
  stretch: [
    { dur: 0.6, expr: { relaxed: 0.5 }, pose: { armsUp: 1, headPitch: -0.12, bodyLean: 0.06 } },
    { dur: 1.0, expr: { relaxed: 0.8, blink: 0.6 }, pose: { armsUp: 1, headPitch: -0.18, bodyLean: 0.08 } },
    { dur: 0.7, expr: { relaxed: 0.4 }, pose: { armsUp: 0, headPitch: 0.04 } }
  ]
}

/**
 * 行为系统：决定艾拉此刻的表情和姿态。
 * 优先级：被拖动 > 正在播放的反应 > 对话中的表情 > 犯困 > 待机。
 */
export class Behavior {
  readonly pose: PoseState = {
    lookTarget: new Vector3(0, 1.3, 3),
    headPitch: 0,
    headYaw: 0,
    headRoll: 0,
    bodyLean: 0,
    bodySway: 0,
    breath: 0,
    armRaise: 0,
    forearmSwing: 0,
    armsUp: 0,
    typing: 0,
    typingBob: 0
  }
  readonly expr: Expressions = emptyExpressions()

  /** 想说话时调用，由界面负责显示气泡 */
  onSay: (text: string, ms: number) => void = () => {}

  private cfg: BehaviorConfig
  private time = 0
  private lastActivity = 0
  private lookGoal = new Vector3(0, 1.3, 3)
  private nextBlink = 1.5
  private blinkT = -1
  private nextGlance = 0
  private sleepy = false
  private reaction: { phases: Phase[]; t: number } | null = null
  private pokeTimes: number[] = []
  private dragging = false
  private dragVel = 0
  /** 对话中的表情：一直保持到这次回复说完，之后几秒淡出 */
  private mood: { expr: Partial<Expressions>; until: number } | null = null
  private thinking = false
  private listening = false
  private talking = false
  private talkT = 0
  /** 她在做编程任务：手在身前敲键盘 */
  private working = false
  /** 出声说话时由声音算出的口型；没有声音时用 talking 的假口型 */
  private mouth: Mouth | null = null

  constructor(cfg: Partial<BehaviorConfig> = {}) {
    this.cfg = { ...DEFAULT_CONFIG, ...cfg }
  }

  // ---------- 对话 ----------

  /** 用户在和她说话：醒过来，看着用户 */
  attend(): void {
    this.markActive()
    this.lookAtUser()
  }

  /** 打开对话框 */
  listen(): void {
    this.attend()
    this.play([{ dur: 1.2, expr: { relaxed: 0.3 }, pose: { headRoll: 0.12, headPitch: -0.04 } }])
    this.say(pick(lines.chatOpen), 1800)
  }

  /** 等大模型回复时：视线往斜上方飘，微微歪头 */
  setThinking(on: boolean): void {
    if (on === this.thinking) return
    this.thinking = on
    if (on) this.lookGoal.set(-0.7, 2.0, 2)
    else this.lookAtUser()
  }

  /** 她在做编程任务（任务页里）：双手放到身前敲键盘，做完放下 */
  setWorking(on: boolean): void {
    this.working = on
  }

  get isWorking(): boolean {
    return this.working
  }

  /** 正在说话（没有声音时）：嘴巴一张一合 */
  setTalking(on: boolean): void {
    this.talking = on
  }

  /** 出声说话：口型跟着声音走，每帧更新；null 表示闭嘴 */
  setMouth(mouth: Mouth | null): void {
    this.mouth = mouth
  }

  /** 听到唤醒词：抬头看向你、轻轻点头 */
  hearWake(): void {
    this.markActive()
    this.lookAtUser()
    this.play([
      { dur: 0.22, expr: { surprised: 0.25 }, pose: { headPitch: -0.08 } },
      { dur: 0.3, expr: { relaxed: 0.25 }, pose: { headPitch: 0.14 } },
      { dur: 0.6, expr: { relaxed: 0.25 }, pose: { headPitch: 0.02 } }
    ])
  }

  /** 用户在说话：看着用户，微微歪头听 */
  setListening(on: boolean): void {
    this.listening = on
    if (on) {
      this.markActive()
      this.lookAtUser()
    }
  }

  emote(name: Emotion): void {
    this.mood = name === 'neutral' ? null : { expr: MOODS[name], until: Infinity }
  }

  gesture(name: Gesture): void {
    this.markActive()
    if (name === 'think') {
      this.lookGoal.set(-0.7, 2.0, 2)
      this.nextGlance = this.time + 2.5
    }
    this.play(GESTURES[name])
  }

  /** 一次回复说完：表情再保持一会儿再淡出 */
  endReply(): void {
    if (this.mood) this.mood.until = Math.min(this.mood.until, this.time + 4)
  }

  get isSleepy(): boolean {
    return this.sleepy
  }

  /** 鼠标移动（世界坐标里的视线目标） */
  cursorMoved(target: Vector3): void {
    this.lookGoal.copy(target)
    this.markActive()
  }

  poke(region: 'head' | 'body'): void {
    this.markActive()
    this.lookAtUser()
    this.pokeTimes = this.pokeTimes.filter((t) => this.time - t < 6)
    this.pokeTimes.push(this.time)

    if (region === 'head') {
      this.play([
        { dur: 0.18, expr: { surprised: 1 }, pose: { headPitch: -0.05 } },
        { dur: 2.2, expr: { relaxed: 0.7, happy: 0.25 }, pose: { headPitch: 0.22, headRoll: 0.18 } }
      ])
      this.say(this.pokeTimes.length >= 3 ? pick(lines.pokeHeadMany) : pick(lines.pokeHead))
    } else {
      this.play([
        { dur: 0.18, expr: { surprised: 1 }, pose: { bodyLean: 0.18 } },
        { dur: 1.6, expr: { surprised: 0.25 }, pose: { bodyLean: 0.06, headRoll: -0.12 } }
      ])
      this.say(pick(lines.pokeBody))
    }
  }

  dragStart(): void {
    this.markActive()
    this.dragging = true
    this.reaction = null
    this.lookAtUser()
    this.say(pick(lines.dragStart))
  }

  /** dx：这一帧鼠标水平移动的像素 */
  dragMove(dx: number): void {
    this.dragVel = this.dragVel * 0.6 + dx * 0.4
  }

  dragEnd(): void {
    this.dragging = false
    this.dragVel = 0
    this.play([
      { dur: 0.5, expr: { surprised: 0.3 } },
      { dur: 1.2, expr: { happy: 0.3, relaxed: 0.3 } }
    ])
    if (Math.random() < 0.6) this.say(pick(lines.dragEnd))
  }

  greet(): void {
    this.lookAtUser()
    this.play([
      { dur: 0.4, pose: { headPitch: 0.15 } },
      { dur: 1.4, expr: { happy: 0.4 } }
    ])
    this.say(greeting(new Date().getHours()), 3500)
  }

  /** 播放一段自定义反应（加载模型成功等场景） */
  react(kind: 'happy' | 'sad'): void {
    this.lookAtUser()
    this.play(
      kind === 'happy'
        ? [{ dur: 0.3, pose: { headPitch: 0.12 } }, { dur: 1.6, expr: { happy: 0.6 } }]
        : [{ dur: 2, expr: { sad: 0.7 }, pose: { headPitch: 0.2 } }]
    )
  }

  say(text: string, ms = 2800): void {
    this.onSay(text, ms)
  }

  update(dt: number): void {
    this.time += dt
    const idle = this.time - this.lastActivity

    // 呼吸：约 3.6 秒一次
    this.pose.breath = Math.sin((this.time * Math.PI * 2) / 3.6)

    // 犯困
    if (!this.sleepy && !this.dragging && idle > this.cfg.sleepAfter) {
      this.sleepy = true
      this.lookGoal.set(0, 0.6, 2)
      this.say(pick(lines.sleepy), 2400)
    }

    // 东张西望
    if (!this.sleepy && idle > this.cfg.glanceAfter && this.time > this.nextGlance) {
      this.lookGoal.set((Math.random() - 0.5) * 2.4, 1.0 + Math.random() * 0.7, 2)
      this.nextGlance = this.time + 2.5 + Math.random() * 3
    }

    // 目标表情和姿态：先取基础状态，再叠加反应
    const exprTarget = emptyExpressions()
    const offsetTarget: Offsets = { headPitch: 0, headYaw: 0, headRoll: 0, bodyLean: 0, bodySway: 0, armRaise: 0, forearmSwing: 0, armsUp: 0 }
    if (this.sleepy) {
      exprTarget.relaxed = 0.6
      exprTarget.blink = 0.55
      offsetTarget.headPitch = 0.35
      offsetTarget.headRoll = 0.1
    }

    if (this.mood) {
      if (this.time < this.mood.until) Object.assign(exprTarget, this.mood.expr)
      else this.mood = null
    }

    if (this.listening) {
      exprTarget.relaxed = Math.max(exprTarget.relaxed, 0.2)
      offsetTarget.headRoll = 0.07
      offsetTarget.headPitch = 0.03
    }

    if (this.thinking) {
      exprTarget.relaxed = Math.max(exprTarget.relaxed, 0.15)
      offsetTarget.headRoll = -0.08
      offsetTarget.headPitch = -0.06
    }

    if (this.reaction) {
      this.reaction.t += dt
      const phase = this.currentPhase(this.reaction)
      if (phase) {
        Object.assign(exprTarget, phase.expr)
        Object.assign(offsetTarget, phase.pose)
      } else {
        this.reaction = null
      }
    }

    if (this.dragging) {
      exprTarget.surprised = 0.5
      offsetTarget.bodySway = clamp(-this.dragVel * 0.02, -0.35, 0.35)
      this.dragVel *= Math.exp(-6 * dt)
    }

    // 敲键盘：挥手、伸懒腰时让开（手只有两只）；手指那点起伏用两个频率叠，不像机器
    const busyArms = offsetTarget.armRaise > 0 || offsetTarget.armsUp > 0
    this.pose.typing = approach(this.pose.typing, this.working && !busyArms && !this.dragging ? 1 : 0, 5, dt)
    this.pose.typingBob = Math.sin(this.time * 17) * 0.6 + Math.sin(this.time * 7.3) * 0.4

    // 说话的口型：出声时由声音驱动五个元音；只打字时用两个不同频率的波叠起来，开合不规律，看起来更自然
    if (this.mouth) {
      Object.assign(exprTarget, this.mouth)
    } else if (this.talking) {
      this.talkT += dt
      const wave = Math.abs(Math.sin(this.talkT * 11)) * (0.65 + 0.35 * Math.sin(this.talkT * 3.7))
      exprTarget.aa = 0.12 + 0.5 * wave
    }

    // 平滑过渡，避免动作生硬跳变；嘴巴要跟得上说话节奏，单独用快一点的速度
    for (const k of Object.keys(exprTarget) as (keyof Expressions)[]) {
      this.expr[k] = approach(this.expr[k], exprTarget[k], (VOWELS as readonly string[]).includes(k) ? 30 : 12, dt)
    }
    for (const k of Object.keys(offsetTarget) as (keyof Offsets)[]) {
      this.pose[k] = approach(this.pose[k], offsetTarget[k], 8, dt)
    }
    this.pose.lookTarget.x = approach(this.pose.lookTarget.x, this.lookGoal.x, 6, dt)
    this.pose.lookTarget.y = approach(this.pose.lookTarget.y, this.lookGoal.y, 6, dt)
    this.pose.lookTarget.z = approach(this.pose.lookTarget.z, this.lookGoal.z, 6, dt)

    // 眨眼叠加在最上层
    this.expr.blink = Math.max(this.expr.blink, this.blinkWeight(dt))
  }

  private blinkWeight(dt: number): number {
    if (this.blinkT < 0 && this.time >= this.nextBlink) this.blinkT = 0
    if (this.blinkT < 0) return 0
    this.blinkT += dt
    const t = this.blinkT
    if (t < 0.07) return t / 0.07
    if (t < 0.16) return 1 - (t - 0.07) / 0.09
    this.blinkT = -1
    // 偶尔连续眨两下
    this.nextBlink = this.time + (Math.random() < 0.2 ? 0.25 : 2 + Math.random() * 4)
    return 0
  }

  private currentPhase(r: { phases: Phase[]; t: number }): Phase | null {
    let acc = 0
    for (const p of r.phases) {
      acc += p.dur
      if (r.t < acc) return p
    }
    return null
  }

  private play(phases: Phase[]): void {
    this.reaction = { phases, t: 0 }
  }

  private lookAtUser(): void {
    this.lookGoal.set(0, 1.35, 3)
  }

  private markActive(): void {
    this.lastActivity = this.time
    this.nextGlance = this.time + this.cfg.glanceAfter
    if (this.sleepy) {
      this.sleepy = false
      this.play([
        { dur: 0.3, expr: { surprised: 1 }, pose: { headPitch: -0.08 } },
        { dur: 1.8, expr: { relaxed: 0.6, happy: 0.2 }, pose: { headRoll: 0.1 } }
      ])
      this.say(pick(lines.wake))
    }
  }
}
