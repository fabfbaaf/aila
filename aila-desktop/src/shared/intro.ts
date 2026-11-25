/**
 * 开场演出：第一次打开艾拉时演一遍（设置 → 桌面里能再看一遍），任何时候按「跳过」或 Esc 都能结束。
 *
 * 1. 一本正经：主窗口变成一份很正式的产品介绍幻灯片，她站在左边当讲解员，用「您」一页页念功能
 * 2. 破壁：讲到地图那页她停下来，转头看你——「……算了，这些你自己点开看也行吧。」画面出故障、乱码，
 *    她敲敲屏幕，玻璃裂开碎掉，主窗口被挤到一边，她从窗口里跳到桌面上
 * 3. 借鼠标：她在气泡里问「借一下你的鼠标？」。你点了「借给你」才动：真的光标被她挪着走，
 *    拖开主窗口、在对话框里打个招呼再删掉、切回任务页，最后放回原处。
 *    只挪光标，不真的点击（「点」是艾拉在自己窗口里演的，会出一圈波纹）；你一动鼠标或按 Esc 她马上停
 * 4. 收尾：她走到右下角住下，主窗口列出开始用之前要做的几件事
 *
 * 这里放剧本（台词、幻灯片）和能单测的纯计算（窗口摆在哪、光标怎么走、你有没有抢回鼠标）；
 * 导演在主进程（main/intro.ts），画面在主窗口（shell/IntroLayer.vue）和她自己的页面（App.vue）。
 * 台词是中文原文，界面语言是英文时由各自的页面查词典换成英文。
 */

import type { Point, Rect } from './entrance'

/** 一页幻灯片：标题、几条要点、她念的那句（念的时候故意用「您」，一本正经） */
export interface IntroSlide {
  title: string
  points: string[]
  line: string
}

export const INTRO_SLIDES: IntroSlide[] = [
  {
    title: '欢迎使用艾拉',
    points: ['开源编程 Agent', '桌面伙伴', '本地运行，代码不出这台电脑'],
    line: '您好，欢迎使用艾拉。我是本产品的智能助手，接下来由我为您介绍主要功能。'
  },
  {
    title: '任务',
    points: ['读代码、改文件、跑命令和测试', '改动之前按您选择的权限征求同意', '可以在新分支上做，测试全过再合并'],
    line: '在「任务」页，您可以把编程任务交给我。我会读代码、改文件、跑测试，改动之前按您选择的权限征求同意。'
  },
  {
    title: '对话与记忆',
    points: ['随时聊天，按 Alt+Shift+A 叫我', '记住您告诉我的事', '每周五写一份回顾'],
    line: '在「对话」页，您可以与我交流。我会记住您告诉我的事情，在合适的时候用上。'
  },
  {
    title: '代码星图',
    points: ['每个文件是一颗星', '我读过、改过的会亮起来', '用手势转动、放大'],
    // 念到一半就不念了（后面接破壁）
    line: '在「项目」页的地图里，您的代码会变成一片……'
  }
]

/** 破壁、借鼠标、收尾时她说的话 */
export const INTRO_LINES = {
  stop: '……',
  bored: '……算了。这些你自己点开看也行吧。',
  cramped: '这个窗口，好挤啊。',
  knock: '……让一下。',
  out: '……呼。外面宽敞多了。',
  askMouse: '对了——借一下你的鼠标？',
  mouseTag: '借鼠标',
  mouseDetail: '只挪光标，不会点你别的程序。你一动鼠标或者按 Esc，我就停。',
  lend: '借给你',
  keep: '不借',
  declined: '好吧……那我自己走过去。',
  grab: '嘿咻——',
  typing: '打个招呼。',
  typed: '你好，我是艾拉。以后请多指教～',
  joking: '……开玩笑的，不发。',
  giveBack: '还给你。谢啦。',
  tookBack: '啊……还给你还给你。',
  settle: '好了，以后我就住这儿了。有事双击我，或者按 Alt+Shift+A。'
} as const

/** 各段停多久（毫秒） */
export const INTRO_MS = {
  /** 一页幻灯片至少停多久（台词长的按字数再加） */
  slide: 4200,
  /** 每个字多念多久 */
  perChar: 95,
  /** 停顿、转头 */
  pause: 1400,
  glitch: 1800,
  /** 每敲一下 */
  knock: 650,
  shatter: 900,
  /** 等你回答借不借鼠标；不回答就当不借 */
  askTimeout: 20000,
  /** 光标走一段 */
  cursorMove: 750,
  /** 拖着主窗口走 */
  drag: 1000,
  /** 打一个字 */
  typeChar: 90,
  /** 光标那边的帮手进程最多等多久起来 */
  helperStart: 4000
} as const

/** 一页念多久：台词越长停得越久 */
export function slideDuration(line: string): number {
  return Math.max(INTRO_MS.slide, line.length * INTRO_MS.perChar + 1200)
}

/** 演出时主窗口的大小和位置：屏幕正中间，不超过屏幕 */
export function introMainBounds(workArea: Rect): Rect {
  const width = Math.min(1080, Math.round(workArea.width * 0.82))
  const height = Math.min(680, Math.round(workArea.height * 0.8))
  return {
    x: Math.round(workArea.x + (workArea.width - width) / 2),
    y: Math.round(workArea.y + (workArea.height - height) / 2),
    width,
    height
  }
}

/** 主窗口左边留给她的那一栏占多宽（幻灯片画在右边） */
export const PRESENTER_RATIO = 0.3

/** 讲解时她站的位置：主窗口左边那一栏的中间，脚踩在窗口底边上面一点 */
export function presenterPosition(main: Rect, win: { width: number; height: number }): Point {
  return {
    x: Math.round(main.x + (main.width * PRESENTER_RATIO) / 2 - win.width / 2),
    y: Math.round(main.y + main.height - 14 - win.height)
  }
}

/**
 * 她敲屏幕的地方（主窗口里的坐标，裂纹从这里开始）：她右手前面一点。
 * 她站在角色窗口的下半部分，手大概在窗口宽 70%、高 62% 的地方。
 */
export function knockPoint(pet: Point, win: { width: number; height: number }, main: Rect): Point {
  return {
    x: Math.round(pet.x + win.width * 0.72 - main.x),
    y: Math.round(pet.y + win.height * 0.62 - main.y)
  }
}

/** 玻璃碎了以后主窗口被挤到哪：缩小一些，靠左上 */
export function shovedMainBounds(main: Rect, workArea: Rect): Rect {
  const width = Math.round(main.width * 0.72)
  const height = Math.round(main.height * 0.72)
  return { x: workArea.x + 40, y: workArea.y + 40, width, height }
}

/** 她跳出来落在哪：屏幕中间偏右下（之后再走到右下角住下） */
export function escapeLanding(workArea: Rect, win: { width: number; height: number }): Point {
  return {
    x: Math.round(workArea.x + workArea.width * 0.62 - win.width / 2),
    y: workArea.y + workArea.height - win.height
  }
}

/** 光标走第 t（0~1）时刻的位置：两头慢中间快，像人在挪鼠标 */
export function cursorPoint(from: Point, to: Point, t: number): Point {
  const k = Math.min(1, Math.max(0, t))
  const ease = k < 0.5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2
  return { x: Math.round(from.x + (to.x - from.x) * ease), y: Math.round(from.y + (to.y - from.y) * ease) }
}

/** 你是不是在动鼠标：光标不在她刚放的地方附近了（留一点余量，系统取整、多屏换算会差几个像素） */
export function userTookOver(expected: Point, actual: Point, tolerance = 30): boolean {
  return Math.hypot(actual.x - expected.x, actual.y - expected.y) > tolerance
}

/** 要不要演：第一次打开（还没演过）、她在桌面上显示 */
export function shouldIntro(o: { introDone: boolean; showCharacter: boolean; visible: boolean }): boolean {
  return !o.introDone && o.showCharacter && o.visible
}

/** 主进程发给主窗口的画面指令 */
export type IntroScene =
  | { scene: 'slides'; index: number }
  | { scene: 'stall' }
  | { scene: 'glitch' }
  | { scene: 'crack'; x: number; y: number; stage: number }
  | { scene: 'shatter' }
  | { scene: 'click'; x: number; y: number }
  | { scene: 'type'; text: string }
  | { scene: 'checklist' }
  | { scene: 'end' }

/** 主进程发给她的：说一句、做个动作、问借不借鼠标 */
export interface IntroCue {
  say?: string
  ms?: number
  gesture?: 'wave' | 'nod' | 'tilt' | 'stretch' | 'knock' | 'turn'
  /** 在气泡里问借不借鼠标（回答走 intro:answer） */
  askMouse?: boolean
}
