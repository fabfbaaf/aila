import {
  AmbientLight,
  Clock,
  DirectionalLight,
  PerspectiveCamera,
  Scene,
  Vector2,
  Vector3,
  WebGLRenderer
} from 'three'
import type { Avatar } from '../avatar/types'
import type { Behavior } from '../behavior/Behavior'

export type HitRegion = 'head' | 'body'

/**
 * 3D 舞台：负责渲染、镜头构图，以及把屏幕坐标换算成世界坐标。
 * 画布背景全透明，桌面能透过来。
 */
export class Stage {
  private renderer: WebGLRenderer
  private scene = new Scene()
  private camera = new PerspectiveCamera(28, 1, 0.1, 20)
  private clock = new Clock()
  private avatar: Avatar | null = null
  private frameId = 0
  private pixel = new Uint8Array(4)
  private tmp = new Vector3()
  private ndc = new Vector2()

  constructor(
    private container: HTMLElement,
    private behavior: Behavior
  ) {
    // preserveDrawingBuffer：渲染完还能读像素，用来判断鼠标是否落在角色身上
    this.renderer = new WebGLRenderer({ alpha: true, antialias: true, preserveDrawingBuffer: true })
    this.renderer.setClearColor(0x000000, 0)
    this.renderer.setPixelRatio(window.devicePixelRatio)
    container.appendChild(this.renderer.domElement)

    // 光照偏柔和：主光从右上前方打过来，环境光补暗部
    const sun = new DirectionalLight(0xffffff, 2.0)
    sun.position.set(1, 1.5, 2).normalize()
    this.scene.add(sun, new AmbientLight(0xffffff, 0.4))

    this.resize()
    window.addEventListener('resize', this.resize)
  }

  setAvatar(avatar: Avatar): void {
    if (this.avatar) {
      this.scene.remove(this.avatar.root)
      this.avatar.dispose()
    }
    this.avatar = avatar
    this.scene.add(avatar.root)
    avatar.update(0, this.behavior.pose, this.behavior.expr)
    avatar.root.updateMatrixWorld(true)
    this.frame()
  }

  start(): void {
    const loop = (): void => {
      this.frameId = requestAnimationFrame(loop)
      this.step(Math.min(this.clock.getDelta(), 0.1))
    }
    loop()
  }

  /** 每帧在行为更新之前调用（对话的打字节奏挂在这里） */
  onStep: (dt: number) => void = () => {}

  /** 推进一帧（也用于调试时手动逐帧检查） */
  step(dt: number): void {
    this.onStep(dt)
    this.behavior.update(dt)
    this.avatar?.update(dt, this.behavior.pose, this.behavior.expr)
    this.renderer.render(this.scene, this.camera)
  }

  dispose(): void {
    cancelAnimationFrame(this.frameId)
    window.removeEventListener('resize', this.resize)
    this.avatar?.dispose()
    this.renderer.dispose()
  }

  /** 显卡信息，用于排查渲染问题 */
  rendererInfo(): string {
    const gl = this.renderer.getContext()
    const ext = gl.getExtension('WEBGL_debug_renderer_info')
    return ext ? String(gl.getParameter(ext.UNMASKED_RENDERER_WEBGL)) : String(gl.getParameter(gl.RENDERER))
  }

  /** 鼠标下面是不是角色：读该点像素的透明度，比射线检测更便宜也更准 */
  hitTest(x: number, y: number): HitRegion | null {
    if (!this.avatar) return null
    const w = this.container.clientWidth
    const h = this.container.clientHeight
    if (x < 0 || y < 0 || x >= w || y >= h) return null

    const ratio = this.renderer.getPixelRatio()
    const gl = this.renderer.getContext()
    gl.readPixels(Math.floor(x * ratio), Math.floor((h - y) * ratio), 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, this.pixel)
    if (this.pixel[3] < 16) return null

    const neckY = this.toScreen(this.avatar.getNeckWorldPosition(this.tmp)).y
    return y < neckY ? 'head' : 'body'
  }

  /** 窗口坐标（可以在窗口外）→ 角色面前的一个世界坐标点，作为视线目标 */
  screenToLookTarget(x: number, y: number, out: Vector3): Vector3 {
    const w = this.container.clientWidth
    const h = this.container.clientHeight
    this.ndc.set((x / w) * 2 - 1, -(y / h) * 2 + 1)
    const dir = this.tmp.set(this.ndc.x, this.ndc.y, 0.5).unproject(this.camera).sub(this.camera.position).normalize()
    const planeZ = 1.2 // 角色正前方的一个虚拟平面
    const t = (planeZ - this.camera.position.z) / dir.z
    return out.copy(this.camera.position).addScaledVector(dir, t)
  }

  private toScreen(world: Vector3): Vector2 {
    const p = world.clone().project(this.camera)
    return new Vector2(
      ((p.x + 1) / 2) * this.container.clientWidth,
      ((1 - p.y) / 2) * this.container.clientHeight
    )
  }

  /** 镜头构图：角色占画面下方约 75%，上方留给对话气泡 */
  private frame(): void {
    if (!this.avatar) return
    const headTop = this.avatar.getHeadWorldPosition(this.tmp).y + 0.14
    const visible = headTop / 0.75
    const dist = visible / 2 / Math.tan((this.camera.fov * Math.PI) / 360)
    const centerY = visible / 2 - 0.06 // 往下多留一点，脚不贴窗口底边
    this.camera.position.set(0, centerY, dist)
    this.camera.lookAt(0, centerY, 0)
    this.camera.updateMatrixWorld()
  }

  private resize = (): void => {
    const w = this.container.clientWidth
    const h = this.container.clientHeight
    this.renderer.setSize(w, h)
    this.camera.aspect = w / h
    this.camera.updateProjectionMatrix()
    this.frame()
  }
}
