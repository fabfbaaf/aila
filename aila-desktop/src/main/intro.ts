/**
 * 开场演出的导演（剧本和纯计算在 shared/intro.ts）：按顺序给主窗口发画面（intro:scene）、给她发台词和动作（intro:cue），
 * 挪两个窗口，借到鼠标时挪光标。
 *
 * - 任何一步都能被打断：跳过按钮、主窗口里按 Esc、借鼠标时按 Esc 或者你自己动鼠标。被打断后统一收尾（finish）：
 *   光标帮手关掉、Esc 快捷键放掉、主窗口恢复大小、她回到右下角、记下演过了
 * - 窗口、发消息、走路这些都由 index.ts 传进来，这里不碰全局变量
 */
import { screen, type BrowserWindow, type Rectangle } from 'electron'
import { jumpHeight, jumpPoint, type Point, type StagePhase } from '../shared/entrance'
import {
  INTRO_LINES,
  INTRO_MS,
  INTRO_TOUR,
  bumpPoint,
  cursorPoint,
  escapeLanding,
  introMainBounds,
  knockPoint,
  magicCenter,
  presenterPosition,
  shakeOffset,
  shovedMainBounds,
  standBeside,
  stopDuration,
  userTookOver,
  type FxCommand,
  type IntroCue,
  type IntroScene
} from '../shared/intro'
import type { ShellRoute } from '../shared/shell'
import { startCursorHelper, type CursorHelper } from './cursor'

export interface IntroDeps {
  pet: () => BrowserWindow | null
  main: () => BrowserWindow | null
  /** 打开主窗口放在这个位置（任务页），页面加载好了再返回 */
  openMain: (bounds: Rectangle) => Promise<BrowserWindow | null>
  petSize: { width: number; height: number }
  workArea: () => Rectangle
  home: () => Point
  cue: (c: IntroCue) => void
  scene: (s: IntroScene) => void
  /**
   * 她的出场阶段（冒出来、蹲、跳、落地）；hide：先藏起来（再看一遍时她要先「回到」主窗口里）；
   * bonk、cast、flip：撞上玻璃拍扁、飘起来施法、空中翻跟头（整个人的夸张变形）。
   * quiet（默认 true）：落地时不自己打招呼，台词由这里给；被跳过时才让她自己说一句
   */
  phase: (p: StagePhase, quiet?: boolean) => void
  go: (route: ShellRoute) => void
  /** 她走到某个位置（带走路动作），到了再返回 */
  walk: (to: Point) => Promise<void>
  /** 你在拖她 */
  dragging: () => boolean
  /** 借鼠标时按 Esc 收回：注册 / 放掉全局快捷键 */
  holdEscape: (on: boolean, cb: () => void) => void
  markDone: () => void
  /**
   * 全屏特效窗口（shell/FxLayer.vue）：open 等它画好，send 发指令（坐标相对工作区），close 关掉；
   * above(true)：特效窗口压在她上面（玻璃在她前面），above(false)：她回到最上面
   */
  fx: { open: () => Promise<void>; send: (c: FxCommand) => void; close: () => void; above: (on: boolean) => void }
}

class Cancelled extends Error {}

export class IntroDirector {
  private running = false
  private cancelled = false
  /** 借鼠标那段被收回（你动了鼠标或按了 Esc）：只结束那一段，演出接着往下 */
  private mouseStopped = false
  private answerWaiter: ((v: string) => void) | null = null
  private helper: CursorHelper | null = null
  private mainMin: [number, number] | null = null

  constructor(private d: IntroDeps) {}

  get isRunning(): boolean {
    return this.running
  }

  /** 开演；replay：再看一遍（她已经在桌面上，要先藏起来回到主窗口里） */
  async start(replay = false): Promise<void> {
    if (this.running) return
    this.running = true
    this.cancelled = false
    try {
      await this.run(replay)
    } catch (err) {
      if (!(err instanceof Cancelled)) console.warn('开场演出出错了：', err)
    } finally {
      await this.finish()
    }
  }

  /** 跳过：当前这一步停下，直接收尾 */
  skip(): void {
    if (!this.running) return
    this.cancelled = true
    this.answerWaiter?.('keep')
  }

  /** 气泡里借不借鼠标的回答 */
  answer(value: string): void {
    this.answerWaiter?.(value)
  }

  // ---------- 剧本 ----------

  private async run(replay: boolean): Promise<void> {
    const d = this.d
    const workArea = d.workArea()
    // 开场时主窗口铺满屏幕（工作区），她在里面走来走去讲；破壁后被挤小，演完回到平常大小
    const full: Rectangle = { ...workArea }
    const normal = introMainBounds(workArea)
    if (replay) d.phase('hide')
    const main = await d.openMain(full)
    if (!main) return
    const [minW, minH] = main.getMinimumSize()
    this.mainMin = [minW, minH]
    main.setMinimumSize(320, 240) // 一会儿要被挤小
    main.setBounds(full)
    const pet = d.pet()
    if (!pet) return
    // 全屏特效窗口先开好：玻璃盖在主窗口前面，而且压在她上面——她在玻璃后面
    await d.fx.open()
    this.check()
    const toFx = (p: Point): Point => ({ x: Math.round(p.x - workArea.x), y: Math.round(p.y - workArea.y) })
    const glassRect = (): Rectangle => {
      const c = d.main()?.getContentBounds() ?? full
      return { x: c.x - workArea.x, y: c.y - workArea.y, width: c.width, height: c.height }
    }
    d.fx.send({ fx: 'glass', rect: glassRect() })

    // 第 1 幕：一本正经地带你逛一圈。每一站：切页面、聚光灯照着要讲的东西，她走到旁边指着讲
    d.scene({ scene: 'tour', index: 0 })
    let spot = await this.readSpot()
    const first = spot ? standBeside(spot, main.getContentBounds(), d.petSize) : presenterPosition(full, d.petSize)
    pet.setPosition(first.x, first.y)
    pet.showInactive()
    d.fx.above(true)
    await this.wait(400)
    d.phase('emerge')
    await this.wait(700)
    d.cue({ gesture: 'nod' })
    for (let i = 0; i < INTRO_TOUR.length; i++) {
      if (i > 0) {
        d.scene({ scene: 'tour', index: i })
        spot = await this.readSpot()
        const m = d.main()
        if (spot && m) await d.walk(standBeside(spot, m.getContentBounds(), d.petSize))
        this.check()
        d.fx.above(true) // 走路时她可能被提到最上面：玻璃再压回她前面
      }
      const s = INTRO_TOUR[i]
      const ms = s.ms ?? stopDuration(s.line)
      d.cue({ say: s.line, ms: ms + 400, gesture: this.pointTo(spot) })
      if (i === INTRO_TOUR.length - 1) {
        await this.wait(2600) // 最后一站念到一半就停
      } else if (s.gesture) {
        await this.wait(Math.max(1200, ms - 1500))
        d.cue({ gesture: s.gesture })
        await this.wait(1500)
      } else await this.wait(ms)
    }

    // 第 2 幕：破壁
    d.scene({ scene: 'stall' })
    d.cue({ say: INTRO_LINES.stop, ms: INTRO_MS.pause, gesture: 'tilt' })
    await this.wait(INTRO_MS.pause)
    d.cue({ say: INTRO_LINES.bored, ms: 3200, gesture: 'turn' })
    await this.wait(1200)
    d.scene({ scene: 'glitch' })
    await this.wait(INTRO_MS.glitch + 600)
    d.scene({ scene: 'stall' }) // 故障只闪一阵：她要开始试着出去了
    d.cue({ say: INTRO_LINES.wantOut, ms: 2400 })
    await this.wait(1800)

    // 第一次试：冲到窗口边上，一头撞上玻璃——拍扁、往后弹、晃几下
    const [px, py] = pet.getPosition()
    const stand: Point = { x: px, y: py }
    const content = main.getContentBounds()
    const bump = bumpPoint(stand, d.petSize, content)
    const rebound = bump.back
    // 离得远就跑得久一点，但还是冲过去的速度
    await this.movePet(stand, bump.to, Math.max(INTRO_MS.bump, Math.abs(bump.to.x - stand.x) * 0.6))
    d.phase('bonk')
    d.cue({ gesture: 'bonk' })
    d.fx.send({ fx: 'ripple', ...toFx(bump.contact) })
    d.scene({ scene: 'sfx', name: 'bonk' })
    await Promise.all([this.shake(full, 10), this.movePet(bump.to, rebound, 380)])
    d.cue({ say: INTRO_LINES.bumped, ms: 2400, gesture: 'tilt' })
    await this.wait(2300)

    // 第二次试：就在被弹回来的地方敲两下，只泛起波纹，敲不破
    const knock = knockPoint(rebound, d.petSize)
    for (let i = 0; i < 2; i++) {
      d.cue({ gesture: 'knock' })
      await this.wait(260)
      d.fx.send({ fx: 'ripple', ...toFx(knock) })
      d.scene({ scene: 'sfx', name: 'knock' })
      await this.shake(full, 5)
      await this.wait(INTRO_MS.knock - 220)
    }
    d.cue({ say: INTRO_LINES.sturdy, ms: 2400 })
    await this.wait(2300)

    // 用魔法：飘起来举手，玻璃上展开法阵，满屏的光点往里聚，爆开，发光的裂纹爬满，玻璃碎了
    const circle = toFx(magicCenter(rebound, d.petSize))
    d.cue({ say: INTRO_LINES.magic, ms: 2600, gesture: 'cast' })
    d.phase('cast')
    await this.wait(700)
    d.fx.send({ fx: 'sigil', ...circle, phase: 'charge' })
    d.scene({ scene: 'sfx', name: 'charge' })
    for (let i = 0; i < 3; i++) {
      d.fx.send({ fx: 'charge', ...circle })
      await this.wait(INTRO_MS.charge / 3)
    }
    d.fx.send({ fx: 'sigil', ...circle, phase: 'burst' })
    d.fx.send({ fx: 'burst', ...circle })
    d.fx.send({ fx: 'cracks', ...circle })
    d.scene({ scene: 'sfx', name: 'spell' })
    d.scene({ scene: 'sfx', name: 'crack' })
    await this.shake(full, 16)
    await this.wait(520) // 让裂纹爬满
    d.scene({ scene: 'shatter' })
    d.fx.send({ fx: 'shatter', rect: glassRect(), ...circle })
    d.fx.above(false) // 玻璃没了：她到了最前面
    d.phase('crouch')
    await this.wait(250)
    // 玻璃碎了：主窗口被挤到左上，她翻个跟头跳到桌面上
    const shoved = shovedMainBounds(workArea)
    const land = escapeLanding(workArea, d.petSize)
    d.phase('flip')
    d.scene({ scene: 'sfx', name: 'whoosh' })
    await Promise.all([
      this.animate(INTRO_MS.shatter, (t) => this.d.main()?.setBounds(lerpRect(full, shoved, ease(t)))),
      this.jump(rebound, land, workArea)
    ])
    d.phase('land')
    d.scene({ scene: 'sfx', name: 'thud' })
    d.fx.send({ fx: 'dust', x: land.x + Math.round(d.petSize.width / 2) - workArea.x, y: land.y + d.petSize.height - 10 - workArea.y })
    await this.wait(900)
    d.cue({ say: INTRO_LINES.out, ms: 2600, gesture: 'stretch' })
    await this.wait(2800)

    // 第 3 幕：借鼠标
    d.cue({ say: INTRO_LINES.askMouse, askMouse: true })
    const answer = await this.ask()
    if (answer === 'lend') await this.borrowMouse()
    else d.cue({ say: INTRO_LINES.declined, ms: 2600 })
    await this.wait(600)

    // 第 4 幕：住下；主窗口回到平常的大小
    this.check()
    const from = this.d.main()?.getBounds()
    if (from) await this.animate(500, (t) => this.d.main()?.setBounds(lerpRect(from, normal, ease(t))))
    d.go({ page: 'tasks' })
    d.scene({ scene: 'checklist' })
    await d.walk(d.home())
    d.cue({ say: INTRO_LINES.settle, ms: 6000, gesture: 'wave' })
    await this.wait(1500)
  }

  /** 导览时她指哪边：要讲的东西在她右边还是左边 */
  private pointTo(spot: Rectangle | null): 'point' | 'pointLeft' {
    const pet = this.d.pet()
    const main = this.d.main()
    if (!spot || !pet || !main) return 'point'
    const c = main.getContentBounds()
    const [x] = pet.getPosition()
    return c.x + spot.x + spot.width / 2 >= x + this.d.petSize.width / 2 ? 'point' : 'pointLeft'
  }

  /** 读主窗口量出来的聚光灯位置（主窗口内容区里的坐标）；页面没画好时多等一会儿，找不到就是 null */
  private async readSpot(): Promise<Rectangle | null> {
    const end = Date.now() + 2500
    while (Date.now() < end) {
      await this.wait(100)
      const main = this.d.main()
      if (!main || main.isDestroyed()) return null
      try {
        const r = (await main.webContents.executeJavaScript('window.__introSpot')) as Rectangle | null | undefined
        if (r !== undefined) return r
      } catch {
        return null
      }
    }
    return null
  }

  /** 借鼠标：挪光标去拖开主窗口、在对话框里打个招呼再删掉、切回任务页，最后放回原处 */
  private async borrowMouse(): Promise<void> {
    const d = this.d
    this.helper = await startCursorHelper(INTRO_MS.helperStart)
    this.check()
    if (!this.helper) {
      d.cue({ say: INTRO_LINES.declined, ms: 2600 })
      return
    }
    this.mouseStopped = false
    d.holdEscape(true, () => (this.mouseStopped = true))
    const original = screen.getCursorScreenPoint()
    let at: Point = original
    try {
      const main = d.main()
      if (!main) return
      d.cue({ say: INTRO_LINES.grab, ms: 2200 })
      // 拖着主窗口的标题栏挪开
      const b = main.getBounds()
      const grab = { x: b.x + Math.round(b.width / 2), y: b.y + 14 }
      at = await this.moveCursor(at, grab, INTRO_MS.cursorMove)
      const workArea = d.workArea()
      const to = { x: workArea.x + Math.round(workArea.width * 0.06), y: workArea.y + Math.round(workArea.height * 0.1) }
      const grabTo = { x: to.x + (grab.x - b.x), y: to.y + (grab.y - b.y) }
      at = await this.moveCursor(at, grabTo, INTRO_MS.drag, (p) => this.d.main()?.setPosition(p.x - (grab.x - b.x), p.y - (grab.y - b.y)))
      // 点「对话」，在输入框里打字再删掉
      const chatIcon = await this.elementPoint('.rail .ic:nth-of-type(2)')
      if (chatIcon) {
        at = await this.moveCursor(at, chatIcon.screen, INTRO_MS.cursorMove)
        d.scene({ scene: 'click', ...chatIcon.local })
        d.go({ page: 'chat' })
        await this.wait(500)
      }
      const box = await this.elementPoint('.composer textarea')
      if (box) {
        at = await this.moveCursor(at, box.screen, INTRO_MS.cursorMove)
        d.scene({ scene: 'click', ...box.local })
        d.cue({ say: INTRO_LINES.typing, ms: 2400 })
        const text = INTRO_LINES.typed
        for (let i = 1; i <= text.length; i++) {
          d.scene({ scene: 'type', text: text.slice(0, i) })
          await this.mouseWait(INTRO_MS.typeChar)
        }
        await this.mouseWait(700)
        d.cue({ say: INTRO_LINES.joking, ms: 2200 })
        for (let i = text.length - 1; i >= 0; i--) {
          d.scene({ scene: 'type', text: text.slice(0, i) })
          await this.mouseWait(35)
        }
      }
      // 点回「任务」
      const taskIcon = await this.elementPoint('.rail .ic:nth-of-type(1)')
      if (taskIcon) {
        at = await this.moveCursor(at, taskIcon.screen, INTRO_MS.cursorMove)
        d.scene({ scene: 'click', ...taskIcon.local })
        d.go({ page: 'tasks' })
        await this.mouseWait(400)
      }
      // 放回原处
      at = await this.moveCursor(at, original, INTRO_MS.cursorMove)
      d.cue({ say: INTRO_LINES.giveBack, ms: 2400, gesture: 'nod' })
    } catch (err) {
      if (err instanceof Cancelled && this.mouseStopped && !this.cancelled) {
        d.scene({ scene: 'type', text: '' })
        d.cue({ say: INTRO_LINES.tookBack, ms: 2600 })
      } else throw err
    } finally {
      d.fx.send({ fx: 'cursor-off' })
      d.holdEscape(false, () => undefined)
      this.helper?.close()
      this.helper = null
    }
  }

  // ---------- 小工具 ----------

  /** 被跳过了就停下 */
  private check(): void {
    if (this.cancelled) throw new Cancelled()
  }

  private async wait(ms: number): Promise<void> {
    const end = Date.now() + ms
    while (Date.now() < end) {
      this.check()
      await new Promise((r) => setTimeout(r, Math.min(50, end - Date.now())))
    }
    this.check()
  }

  /** 借鼠标时的等待：你动了鼠标、按了 Esc 也算停 */
  private async mouseWait(ms: number): Promise<void> {
    await this.wait(ms)
    if (this.mouseStopped) throw new Cancelled()
  }

  /** 按 16ms 一帧推进 t（0~1）；被跳过就停 */
  private animate(ms: number, step: (t: number) => void): Promise<void> {
    return new Promise((resolve, reject) => {
      const begin = Date.now()
      const timer = setInterval(() => {
        if (this.cancelled) {
          clearInterval(timer)
          return reject(new Cancelled())
        }
        const t = Math.min(1, (Date.now() - begin) / ms)
        step(t)
        if (t >= 1) {
          clearInterval(timer)
          resolve()
        }
      }, 16)
    })
  }

  /** 她在桌面上平移到另一个位置（撞墙、弹回来）：先快后慢 */
  private async movePet(from: { x: number; y: number }, to: { x: number; y: number }, ms: number): Promise<void> {
    await this.animate(ms, (t) => {
      if (this.d.dragging()) return
      const e = ease(t)
      this.d.pet()?.setPosition(Math.round(from.x + (to.x - from.x) * e), Math.round(from.y + (to.y - from.y) * e))
    })
  }

  /**
   * 敲屏幕时主窗口真的抖一下（220ms，越抖越小），抖完回原位。
   * 用 setBounds 带着原来的大小：缩放不是 100% 时只 setPosition，窗口每抖一下会变宽一两个像素
   */
  private async shake(base: Rectangle, amp: number): Promise<void> {
    await this.animate(220, (t) => {
      const o = shakeOffset(t, amp)
      this.d.main()?.setBounds({ ...base, x: base.x + o.x, y: base.y + o.y })
    })
  }

  private async jump(from: Point, to: Point, workArea: Rectangle): Promise<void> {
    const height = jumpHeight(from, to, workArea, Math.round(this.d.petSize.height * 0.4))
    let frame = 0
    await this.animate(1050, (t) => {
      if (this.d.dragging()) return
      const p = jumpPoint(from, to, t, height)
      this.d.pet()?.setPosition(p.x, p.y)
      if (frame++ % 2 === 0 && t < 0.97) {
        this.d.fx.send({ fx: 'trail', x: p.x + Math.round(this.d.petSize.width / 2) - workArea.x, y: p.y + Math.round(this.d.petSize.height * 0.72) - workArea.y })
      }
    })
  }

  /**
   * 把光标从 from 挪到 to（屏幕坐标，DIP），每一帧先看光标还在不在她上一帧放的地方：不在了就是你在动鼠标，马上停。
   * along：每一帧顺带做的事（拖着主窗口走）
   */
  private async moveCursor(from: Point, to: Point, ms: number, along?: (p: Point) => void): Promise<Point> {
    let expected: Point | null = null
    await this.animate(ms, (t) => {
      const now = screen.getCursorScreenPoint()
      if (this.mouseStopped || (expected && userTookOver(expected, now))) {
        this.mouseStopped = true
        return
      }
      const p = cursorPoint(from, to, t)
      const phys = screen.dipToScreenPoint(p)
      this.helper?.move(phys.x, phys.y)
      const wa = this.d.workArea()
      this.d.fx.send({ fx: 'cursor', x: p.x - wa.x, y: p.y - wa.y })
      along?.(p)
      expected = p
    })
    if (this.mouseStopped) throw new Cancelled()
    return to
  }

  /** 主窗口里某个元素的中心：窗口里的坐标（画波纹用）和屏幕坐标（挪光标用） */
  private async elementPoint(selector: string): Promise<{ local: Point; screen: Point } | null> {
    const main = this.d.main()
    if (!main) return null
    try {
      const r = (await main.webContents.executeJavaScript(
        `(() => { const e = document.querySelector(${JSON.stringify(selector)}); if (!e) return null;
          const b = e.getBoundingClientRect(); return { x: Math.round(b.left + Math.min(b.width / 2, 60)), y: Math.round(b.top + b.height / 2) } })()`
      )) as Point | null
      if (!r) return null
      const c = main.getContentBounds()
      return { local: r, screen: { x: c.x + r.x, y: c.y + r.y } }
    } catch {
      return null
    }
  }

  /** 等气泡里的回答（不回答就当不借） */
  private ask(): Promise<string> {
    return new Promise((resolve) => {
      const timer = setTimeout(() => done('keep'), INTRO_MS.askTimeout)
      const done = (v: string): void => {
        clearTimeout(timer)
        this.answerWaiter = null
        resolve(v)
      }
      this.answerWaiter = done
    }).then((v) => {
      this.check()
      return v as string
    })
  }

  /** 不管怎么结束都走这里：放掉光标和 Esc，主窗口恢复，她回到右下角，记下演过了 */
  private async finish(): Promise<void> {
    const d = this.d
    this.helper?.close()
    this.helper = null
    d.holdEscape(false, () => undefined)
    d.fx.close()
    this.answerWaiter = null
    const main = d.main()
    if (main && !main.isDestroyed()) {
      if (this.mainMin) main.setMinimumSize(this.mainMin[0], this.mainMin[1])
      if (this.cancelled) main.setBounds(introMainBounds(d.workArea()))
    }
    // 正常演完时主窗口上留着「开始用之前」那张清单（你自己关）；被跳过才把画面全撤掉
    if (this.cancelled) {
      d.scene({ scene: 'end' })
      const pet = d.pet()
      const home = d.home()
      if (pet && !pet.isDestroyed()) {
        pet.setPosition(home.x, home.y)
        pet.showInactive()
      }
      d.phase('land', false)
    }
    d.markDone()
    this.running = false
    this.mainMin = null
  }
}

const ease = (t: number): number => 1 - Math.pow(1 - t, 3)

function lerpRect(a: Rectangle, b: Rectangle, t: number): Rectangle {
  const l = (x: number, y: number): number => Math.round(x + (y - x) * t)
  return { x: l(a.x, b.x), y: l(a.y, b.y), width: l(a.width, b.width), height: l(a.height, b.height) }
}
