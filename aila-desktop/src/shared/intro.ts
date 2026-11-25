/**
 * 开场演出：第一次打开艾拉时演一遍（设置 → 桌面里能再看一遍），任何时候按「跳过」或 Esc 都能结束。
 *
 * 1. 一本正经：主窗口变成一份很正式的产品介绍幻灯片（每页一个小演示），她站在左边当讲解员，用「您」一页页念功能，
 *    越念越随便：打哈欠、念得飞快
 * 2. 破壁（用魔法）：讲到地图那页她停下来，转头看你——「……算了，这些你自己点开看也行吧。」画面出故障、乱码。
 *    她想出去：先往窗口边上走，撞上一层看不见的玻璃被弹回来（撞到的地方漾开一圈屏障的波纹）；
 *    再敲两下，只泛波纹、敲不破；于是举起手放魔法——面前展开一个法阵，满屏的光点往里聚，
 *    法阵爆开，发光的裂纹从法阵往外爬，玻璃碎了，她带着一串火花跳到桌面上
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

/** 每页幻灯片上演的小演示（画面在 shell/IntroLayer.vue） */
export type IntroDemo = 'welcome' | 'terminal' | 'diff' | 'memory' | 'voice' | 'works' | 'ext' | 'stars'

/**
 * 一页幻灯片：标题、几条要点、小演示、她念的那句。
 * 越讲越随便：前几页规规矩矩用「您」，讲到语音那页打个哈欠，后面越念越快，地图那页念到一半就不念了。
 * ms：这一页停多久（不给就按台词长短算）；gesture：念完以后做的动作
 */
export interface IntroSlide {
  title: string
  points: string[]
  demo: IntroDemo
  line: string
  ms?: number
  gesture?: 'yawn' | 'tilt' | 'nod'
}

export const INTRO_SLIDES: IntroSlide[] = [
  {
    title: '欢迎使用艾拉',
    points: ['开源编程 Agent', '桌面伙伴', '本地运行，代码不出这台电脑'],
    demo: 'welcome',
    line: '您好，欢迎使用艾拉。我是本产品的智能助手，接下来由我为您介绍主要功能。'
  },
  {
    title: '任务',
    points: ['读代码、改文件、跑命令和测试', '一直做到测试全过', '终端和桌面看同一份进度'],
    demo: 'terminal',
    line: '在「任务」页，您可以把编程任务交给我。我会读代码、改文件、跑测试，直到做完为止。'
  },
  {
    title: '权限与分支',
    points: ['只读 / 先问再改 / 工作区内自动', '删除、推送这类危险命令一定先问', '在新分支上做，测试全过再合并'],
    demo: 'diff',
    line: '每次改动之前，我都会按您选择的权限征求同意。您也可以让我在新分支上做，测试全部通过再合并。'
  },
  {
    title: '对话与记忆',
    points: ['随时聊天，按 Alt+Shift+A 叫我', '记住您告诉我的事', '登录后几台电脑之间同步'],
    demo: 'memory',
    line: '在「对话」页，您可以与我交流。我会记住您告诉我的事情……比如您不喝咖啡。'
  },
  {
    title: '语音与屏幕',
    points: ['说「你好艾拉」叫我', '看到屏幕上的报错，主动问要不要帮忙', '摄像头、麦克风、屏幕都要您打开开关才用'],
    demo: 'voice',
    line: '说一声「你好艾拉」就能叫我。开着看屏幕的话，看到报错我会主动问您要不要帮忙……',
    gesture: 'yawn'
  },
  {
    title: '作品、日记、周报',
    points: ['隔几天自己做个小作品', '每天晚上写日记', '每周五写一份能当周报的回顾'],
    demo: 'works',
    line: '我还会自己做点小作品，每天写日记，每周五写回顾。嗯。',
    gesture: 'tilt'
  },
  {
    title: '终端和扩展',
    points: ['终端里打 aila 也能用', '装扩展：命令、本事、工具服务', '接 MCP 外部工具'],
    demo: 'ext',
    line: '终端、扩展、MCP，都有。下一页。',
    ms: 2600
  },
  {
    title: '代码星图',
    points: ['每个文件是一颗星', '我读过、改过的会亮起来', '用手势转动、放大'],
    demo: 'stars',
    // 念到一半就不念了（后面接破壁）
    line: '在「项目」页的地图里，您的代码会变成一片……'
  }
]

/** 破壁、借鼠标、收尾时她说的话 */
export const INTRO_LINES = {
  stop: '……',
  bored: '……算了。这些你自己点开看也行吧。',
  cramped: '这个窗口，好挤啊。',
  wantOut: '……我想出去看看。',
  bumped: '……诶？出不去？',
  sturdy: '这层玻璃……有点结实。',
  magic: '那就——用魔法吧。',
  out: '……成功了。外面宽敞多了。',
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
  /** 往窗口边上走、撞上去 */
  bump: 700,
  /** 法阵蓄力 */
  charge: 2600,
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

/**
 * 第一次试着出去：往主窗口左边走，走到身子贴着窗口边（中心离左边 40 像素）撞上去。
 * to：角色窗口要走到的位置；contact：撞到的地方（主窗口里的坐标，画波纹用）
 */
export function bumpPoint(stand: Point, win: { width: number; height: number }, main: Rect): { to: Point; contact: Point } {
  const centerX = main.x + 40
  return {
    to: { x: Math.round(centerX - win.width / 2), y: stand.y },
    contact: { x: 14, y: Math.round(stand.y + win.height * 0.62 - main.y) }
  }
}

/** 法阵画在哪（主窗口里的坐标）：她伸出去的手前面一点，在她和幻灯片之间 */
export function magicCenter(stand: Point, win: { width: number; height: number }, main: Rect): Point {
  const hand = knockPoint(stand, win, main)
  return { x: Math.round(hand.x + 70), y: Math.round(hand.y - 30) }
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

/** 音效（主窗口里用 Web Audio 现合成，shell/introSfx.ts） */
export type SfxName = 'page' | 'type' | 'glitch' | 'knock' | 'crack' | 'shatter' | 'whoosh' | 'thud' | 'click' | 'chime' | 'bonk' | 'charge' | 'spell'

/** 主进程发给主窗口的画面指令 */
export type IntroScene =
  | { scene: 'slides'; index: number }
  | { scene: 'stall' }
  | { scene: 'glitch' }
  | { scene: 'crack'; x: number; y: number; stage: number; magic?: boolean }
  /** 撞上、敲到看不见的玻璃：那里漾开一圈屏障的波纹（敲不破） */
  | { scene: 'ripple'; x: number; y: number }
  /** 法阵：charge 展开、转着蓄力；burst 爆开（接着就是裂纹和碎） */
  | { scene: 'magic'; x: number; y: number; phase: 'charge' | 'burst' }
  | { scene: 'shatter' }
  | { scene: 'click'; x: number; y: number }
  | { scene: 'type'; text: string }
  | { scene: 'sfx'; name: SfxName }
  | { scene: 'checklist' }
  | { scene: 'end' }

/** 主进程发给她的：说一句、做个动作、问借不借鼠标 */
export interface IntroCue {
  say?: string
  ms?: number
  gesture?: 'wave' | 'nod' | 'tilt' | 'stretch' | 'knock' | 'turn' | 'point' | 'yawn' | 'bonk' | 'cast'
  /** 在气泡里问借不借鼠标（回答走 intro:answer） */
  askMouse?: boolean
}

// ---------- 全屏特效（玻璃碎片飞出窗口、跳跃的轨迹、落地的灰、借鼠标时光标外的光圈） ----------
// 特效窗口铺满整个工作区、透明、点不到（shell/FxLayer.vue），坐标都是相对工作区左上角的

/** 主进程发给特效窗口的指令 */
export type FxCommand =
  | { fx: 'shatter'; rect: Rect; x: number; y: number }
  | { fx: 'trail'; x: number; y: number }
  | { fx: 'dust'; x: number; y: number }
  /** 蓄力：满屏的光点往这一点聚过来 */
  | { fx: 'charge'; x: number; y: number }
  /** 法阵爆开：一圈光往外扩 */
  | { fx: 'burst'; x: number; y: number }
  | { fx: 'cursor'; x: number; y: number }
  | { fx: 'cursor-off' }
  | { fx: 'clear' }

/** 一个粒子：碎片（三角形）、火花、灰 */
export interface Particle {
  kind: 'shard' | 'spark' | 'dust' | 'charge'
  x: number
  y: number
  vx: number
  vy: number
  rot: number
  vr: number
  /** 还剩几秒 */
  life: number
  max: number
  size: number
  /** 碎片的三个角（相对中心） */
  shape?: [number, number][]
}

/** 有种子的随机数（mulberry32）：同一个种子出来的碎片一样，测试和每次播放都稳定 */
export function seeded(seed: number): () => number {
  let a = seed >>> 0
  return () => {
    a = (a + 0x6d2b79f5) >>> 0
    let t = a
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

/** 玻璃碎了：窗口范围里散出一堆三角碎片，从裂纹中心往外飞、先往上蹦一下再掉下去 */
export function spawnShards(rect: Rect, origin: Point, count: number, rand: () => number): Particle[] {
  const out: Particle[] = []
  for (let i = 0; i < count; i++) {
    const x = rect.x + rand() * rect.width
    const y = rect.y + rand() * rect.height
    const dx = x - origin.x
    const dy = y - origin.y
    const d = Math.hypot(dx, dy) || 1
    const speed = 380 + rand() * 820
    const size = 14 + rand() * 46
    const shape: [number, number][] = [0, 1, 2].map((k) => {
      const a = (k / 3) * Math.PI * 2 + rand() * 1.4
      const r = size * (0.5 + rand() * 0.6)
      return [Math.cos(a) * r, Math.sin(a) * r]
    })
    const life = 1.6 + rand() * 1.2
    out.push({ kind: 'shard', x, y, vx: (dx / d) * speed, vy: (dy / d) * speed - 260 - rand() * 380, rot: rand() * Math.PI, vr: (rand() - 0.5) * 12, life, max: life, size, shape })
  }
  return out
}

/** 落地时脚边扬起的一小撮灰：往两边散开、慢慢往上飘、变淡 */
export function spawnDust(at: Point, count: number, rand: () => number): Particle[] {
  return Array.from({ length: count }, () => {
    const side = rand() < 0.5 ? -1 : 1
    const life = 0.5 + rand() * 0.5
    return { kind: 'dust' as const, x: at.x + side * rand() * 30, y: at.y - rand() * 6, vx: side * (60 + rand() * 160), vy: -(20 + rand() * 70), rot: 0, vr: 0, life, max: life, size: 6 + rand() * 12 }
  })
}

/** 跳跃轨迹上的一颗火花：原地闪一下、往下掉一点就没了 */
export function spawnSpark(at: Point, rand: () => number): Particle {
  const life = 0.45 + rand() * 0.35
  return { kind: 'spark', x: at.x + (rand() - 0.5) * 40, y: at.y + (rand() - 0.5) * 40, vx: (rand() - 0.5) * 60, vy: 20 + rand() * 60, rot: 0, vr: 0, life, max: life, size: 2 + rand() * 4 }
}

/**
 * 蓄力的光点：从四周（离中心 220~560 像素的一圈）出发，各自在寿命走完时正好到中心，越近越快（加速聚拢）。
 * 用匀速近似：速度 = 距离 / 寿命；FxLayer 画的时候越接近中心越亮
 */
export function spawnCharge(center: Point, count: number, rand: () => number): Particle[] {
  return Array.from({ length: count }, () => {
    const a = rand() * Math.PI * 2
    const r = 220 + rand() * 340
    const life = 0.8 + rand() * 1.4
    const x = center.x + Math.cos(a) * r
    const y = center.y + Math.sin(a) * r
    return { kind: 'charge' as const, x, y, vx: (center.x - x) / life, vy: (center.y - y) / life, rot: 0, vr: 0, life, max: life, size: 1.5 + rand() * 2.5 }
  })
}

/** 往前走 dt 秒：碎片受重力掉得快，灰几乎不受重力，火花轻轻往下掉 */
export function stepParticle(p: Particle, dt: number): Particle {
  const gravity = p.kind === 'shard' ? 1700 : p.kind === 'spark' ? 220 : p.kind === 'charge' ? 0 : -30
  const drag = p.kind === 'dust' ? Math.pow(0.15, dt) : 1
  return { ...p, x: p.x + p.vx * dt, y: p.y + p.vy * dt, vx: p.vx * drag, vy: p.vy * drag + gravity * dt, rot: p.rot + p.vr * dt, life: p.life - dt }
}

/** 还要不要画：没到寿命、没掉出屏幕底下太多 */
export function particleAlive(p: Particle, height: number): boolean {
  return p.life > 0 && p.y < height + 120
}

/** 敲屏幕时窗口抖一下：第 t（0~1）时刻偏多少，越抖越小 */
export function shakeOffset(t: number, amp: number): Point {
  if (t >= 1 || t < 0) return { x: 0, y: 0 }
  const fade = 1 - t
  return { x: Math.round(Math.sin(t * Math.PI * 9) * amp * fade), y: Math.round(Math.cos(t * Math.PI * 7) * amp * 0.5 * fade) }
}
