/**
 * 开场演出：第一次打开艾拉时演一遍（设置 → 桌面里能再看一遍），任何时候按「跳过」或 Esc 都能结束。
 *
 * 1. 一本正经地带你逛一圈：主窗口一页页切过去（任务、对话、记忆、作品、设置），她走到要讲的东西旁边，
 *    那一块被聚光灯照亮，她指着它用「您」讲；越讲越随便：打哈欠、念得飞快。
 *    整个主窗口前面隔着一层玻璃（全屏特效层画的反光），她在玻璃后面——就像被关在应用里
 * 2. 破壁（用魔法）：讲到地图那页她停下来，转头看你——「……算了，这些你自己点开看也行吧。」画面出故障。
 *    她想出去：先往窗口边上走，撞上玻璃被弹回来（缩成一团、晃两下；玻璃上漾开屏障的波纹）；
 *    再敲两下，只泛波纹、敲不破；于是举起手、飘起来放魔法——玻璃上展开一个法阵，满屏的光点往里聚，
 *    法阵爆开，发光的裂纹爬满玻璃，玻璃碎了，她翻个跟头带着一串火花跳到桌面上，这才到了玻璃前面
 * 3. 借鼠标：她在气泡里问「借一下你的鼠标？」。你点了「借给你」才动：真的光标被她挪着走，
 *    拖开主窗口、在对话框里打个招呼再删掉、切回任务页，最后放回原处。
 *    只挪光标，不真的点击（「点」是艾拉在自己窗口里演的，会出一圈波纹）；你一动鼠标或按 Esc 她马上停
 * 4. 收尾：她走到右下角住下，主窗口列出开始用之前要做的几件事
 *
 * 这里放剧本（导览的每一站、台词）和能单测的纯计算（窗口摆在哪、她站哪、光标怎么走、你有没有抢回鼠标）；
 * 导演在主进程（main/intro.ts），画面在主窗口（shell/IntroLayer.vue）、全屏特效层（shell/FxLayer.vue）和她自己的页面（App.vue）。
 * 台词是中文原文，界面语言是英文时由各自的页面查词典换成英文。
 */

import type { Point, Rect } from './entrance'
import type { ShellRoute } from './shell'

/**
 * 导览的一站：切到哪一页、聚光灯照哪个元素（几个选择器，用第一个看得见的）、旁边的小标题、她念的那句。
 * 越讲越随便：前几站规规矩矩用「您」，讲到记忆那站打个哈欠，后面越念越快，地图那站念到一半就不念了。
 * ms：这一站停多久（不给就按台词长短算）；gesture：念完以后做的动作
 */
export interface TourStop {
  route: ShellRoute
  selector: string
  title: string
  line: string
  ms?: number
  gesture?: 'yawn' | 'tilt' | 'nod'
}

export const INTRO_TOUR: TourStop[] = [
  {
    route: { page: 'tasks' },
    selector: '.rail',
    title: '欢迎使用艾拉',
    line: '您好，欢迎使用艾拉。我是本产品的智能助手，接下来由我带您熟悉一下各个页面。'
  },
  {
    route: { page: 'tasks' },
    selector: '.side .new',
    title: '任务',
    line: '在「任务」页点「新任务」，就可以把编程任务交给我。我会读代码、改文件、跑测试，改动之前按您选择的权限征求同意。'
  },
  {
    route: { page: 'chat' },
    selector: '.composer',
    title: '对话',
    line: '在「对话」页，您可以与我交流。我会记住您告诉我的事情……比如您不喝咖啡。'
  },
  {
    route: { page: 'memory' },
    selector: '.card.add',
    title: '记忆',
    line: '「记忆」页里是我记得的事。您也可以直接告诉我，或者让我忘掉……',
    gesture: 'yawn'
  },
  {
    route: { page: 'studio' },
    selector: '.bar .tabs',
    title: '作品、日记、回顾',
    line: '我还会自己做点小作品，每天写日记，每周五写回顾。嗯。',
    gesture: 'tilt'
  },
  {
    route: { page: 'settings', section: 'voice' },
    selector: 'nav.side',
    title: '设置',
    line: '语音、看屏幕、扩展，都在设置里。下一个。',
    ms: 2800
  },
  {
    route: { page: 'projects', tab: 'map' },
    selector: '.wb .canvas',
    title: '代码星图',
    // 念到一半就不念了（后面接破壁）
    line: '在「项目」页的地图里，您的代码会变成一片……'
  }
]

/** 她站在聚光灯照着的东西旁边：画在哪（主窗口内容区里的坐标）、她在左边还是右边 */
export function standBeside(
  spot: Rect,
  content: Rect,
  win: { width: number; height: number }
): Point {
  // 东西在左半边：她站右边；在右半边：她站左边。离东西留 70 像素。
  // 东西太大（比如铺满页面的星图）旁边站不下：她站到正中间、东西前面。整个人不出窗口
  const right = spot.x + spot.width / 2 < content.width / 2
  const want = spot.width > content.width * 0.6 ? content.width / 2 : right ? spot.x + spot.width + 70 : spot.x - 70
  const half = win.width / 2
  const cx = Math.min(content.width - half, Math.max(half, want))
  // 脚踩在东西下面一点；她的头不出窗口顶（角色窗口顶最多出去 10 像素，那里是空的），脚不出窗口底
  const feet = Math.min(content.height - 12, Math.max(win.height - 10, spot.y + spot.height + 30))
  return { x: Math.round(content.x + cx - win.width / 2), y: Math.round(content.y + feet - win.height) }
}

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

/** 一站念多久：台词越长停得越久 */
export function stopDuration(line: string): number {
  return Math.max(INTRO_MS.slide, line.length * INTRO_MS.perChar + 1200)
}

/** 演完（或被跳过）时主窗口回到的平常大小：屏幕正中间，不超过屏幕 */
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
 * 她敲玻璃的地方（屏幕坐标，玻璃画在她前面的全屏特效层上）：她的手。
 * 她站在角色窗口的下半部分，手大概在窗口宽 72%、高 62% 的地方
 */
export function knockPoint(pet: Point, win: { width: number; height: number }): Point {
  return { x: Math.round(pet.x + win.width * 0.72), y: Math.round(pet.y + win.height * 0.62) }
}

/**
 * 第一次试着出去：往离她近的那一边窗口边上走，走到身子贴着边（中心离边 40 像素）撞上去。
 * to：角色窗口要走到的位置；contact：撞到的地方（屏幕坐标，玻璃上画波纹用）
 */
export function bumpPoint(pet: Point, win: { width: number; height: number }, main: Rect): { to: Point; contact: Point; back: Point } {
  const left = pet.x + win.width / 2 < main.x + main.width / 2
  const centerX = left ? main.x + 40 : main.x + main.width - 40
  const to = { x: Math.round(centerX - win.width / 2), y: pet.y }
  return {
    to,
    contact: { x: left ? main.x + 10 : main.x + main.width - 10, y: Math.round(pet.y + win.height * 0.62) },
    // 被弹回来落在哪：离边 200 像素（之后就在这儿敲、放魔法）
    back: { x: to.x + (left ? 160 : -160), y: pet.y }
  }
}

/** 法阵画在哪（屏幕坐标）：玻璃上、正对着她胸口——她在法阵后面施法 */
export function magicCenter(pet: Point, win: { width: number; height: number }): Point {
  return { x: Math.round(pet.x + win.width / 2), y: Math.round(pet.y + win.height * 0.58) }
}

/** 玻璃碎了以后铺满屏幕的主窗口被挤到哪：缩到一半多一点，靠左上，右下空出来给她落脚 */
export function shovedMainBounds(workArea: Rect): Rect {
  const width = Math.round(workArea.width * 0.55)
  const height = Math.round(workArea.height * 0.62)
  return { x: workArea.x + 40, y: workArea.y + 40, width, height }
}

/** 她跳出来落在哪：屏幕中间偏右下（之后再走到右下角住下） */
export function escapeLanding(workArea: Rect, win: { width: number; height: number }): Point {
  return {
    x: Math.round(workArea.x + workArea.width * 0.74 - win.width / 2),
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
  | { scene: 'stall' }
  | { scene: 'glitch' }
  /** 导览：切到第 index 站，聚光灯照着那一站的元素 */
  | { scene: 'tour'; index: number }
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
  gesture?: 'wave' | 'nod' | 'tilt' | 'stretch' | 'knock' | 'turn' | 'point' | 'pointLeft' | 'yawn' | 'bonk' | 'cast'
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
  /** 主窗口前面的那层玻璃（她在玻璃后面）：on 盖上，rect 是主窗口内容区 */
  | { fx: 'glass'; rect: Rect }
  | { fx: 'glass-off' }
  /** 撞上、敲到玻璃：那里漾开几圈屏障的波纹 */
  | { fx: 'ripple'; x: number; y: number }
  /** 玻璃上的法阵：charge 展开、转着蓄力；burst 爆开 */
  | { fx: 'sigil'; x: number; y: number; phase: 'charge' | 'burst' }
  /** 法阵爆开后玻璃上爬满发光的裂纹 */
  | { fx: 'cracks'; x: number; y: number }
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

/**
 * 法阵爆开后玻璃上的裂纹：从中心往四周放射的折线（SVG path），越往外越歪，末梢分叉；
 * 每条一直爬到碰到玻璃的边才停，所以不管法阵在哪，裂纹都能爬满整块玻璃。rand 固定时每次画出来一样
 */
export function magicCracks(center: Point, rect: Rect, count: number, rand: () => number): string[] {
  const inside = (x: number, y: number): boolean => x >= rect.x && x <= rect.x + rect.width && y >= rect.y && y <= rect.y + rect.height
  const clampX = (x: number): number => Math.min(rect.x + rect.width, Math.max(rect.x, x))
  const clampY = (y: number): number => Math.min(rect.y + rect.height, Math.max(rect.y, y))
  const paths: string[] = []
  for (let i = 0; i < count; i++) {
    let angle = (i / count) * Math.PI * 2 + (rand() - 0.5) * 0.4
    let x = center.x
    let y = center.y
    let d = `M${Math.round(x)} ${Math.round(y)}`
    for (let s = 0; s < 40 && inside(x, y); s++) {
      angle += (rand() - 0.5) * 0.7
      const len = 30 + rand() * 50 + s * 6
      x = clampX(x + Math.cos(angle) * len)
      y = clampY(y + Math.sin(angle) * len)
      d += ` L${Math.round(x)} ${Math.round(y)}`
      // 偶尔分一个短叉
      if (s > 1 && rand() < 0.3) {
        const b = angle + (rand() < 0.5 ? -1 : 1) * (0.5 + rand() * 0.6)
        const bl = 20 + rand() * 40
        paths.push(`M${Math.round(x)} ${Math.round(y)} L${Math.round(clampX(x + Math.cos(b) * bl))} ${Math.round(clampY(y + Math.sin(b) * bl))}`)
      }
      if (x === rect.x || x === rect.x + rect.width || y === rect.y || y === rect.y + rect.height) break
    }
    paths.unshift(d)
  }
  return paths
}
