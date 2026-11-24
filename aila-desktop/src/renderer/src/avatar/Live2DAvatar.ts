/**
 * Live2D 角色：和 VRM 角色实现同一个 Avatar 接口，舞台（Stage）、点击判断、镜头构图、行为系统都不用改。
 *
 * 怎么画：Live2D 模型用 PixiJS + pixi-live2d-display 画到一张离屏的画布上，再把这张画布当贴图贴在 three.js
 * 场景里的一个平面上。这样鼠标点没点到她（读像素透明度）、视线、省电降帧都还是原来那一套。
 *
 * 为什么运行库要另外装：Live2D 的 Cubism Core 是 Live2D 公司的专有软件，要同意它的许可协议才能用，
 * 不能放进开源仓库。npm run setup:live2d 会先给你看许可、你同意了才下载到 public/live2d/（不进 Git）。
 * 运行库要在加载 pixi-live2d-display 之前就挂在 window 上，所以这里按需插 <script>，再动态 import。
 *
 * 模型文件：主进程把 .model3.json 所在文件夹里的文件读给我们（ModelFiles），这里包成带相对路径的 File，
 * 交给 pixi-live2d-display 自己的 FileLoader 解析（贴图、动作、物理都按 model3.json 里写的相对路径找）。
 */
import { CanvasTexture, Mesh, MeshBasicMaterial, Object3D, PlaneGeometry, SRGBColorSpace, Vector3 } from 'three'
import type { ModelFiles } from '../../../shared/model'
import { live2dParams, lookFrom } from './live2dParams'
import type { Avatar, Expressions, PoseState } from './types'
import { t } from '../i18n'

/** 世界坐标里她有多高（和 VRM 模型差不多，镜头构图不用改） */
const WORLD_HEIGHT = 1.55
/** 离屏画布的高度（像素）：窗口高 480，画两倍清楚一点 */
const CANVAS_HEIGHT = 1000
/** 头和脖子在模型里大概的高度（从上往下的比例）：没有统一的标注，按常见的立绘比例估 */
const HEAD_FROM_TOP = 0.17
const NECK_FROM_TOP = 0.3
/** 呼吸交给模型自带的（它会叠到好几个参数上），我们不覆盖 */
const SKIP = new Set(['ParamBreath'])
const CORE_URL = './live2d/live2dcubismcore.min.js'

export class Live2DUnavailable extends Error {}

declare global {
  interface Window {
    Live2DCubismCore?: unknown
  }
}

/** 运行库：没装就报一句说清楚怎么装 */
async function loadCore(): Promise<void> {
  if (window.Live2DCubismCore) return
  await new Promise<void>((resolve, reject) => {
    const s = document.createElement('script')
    s.src = new URL(CORE_URL, location.href).href
    s.onload = () => resolve()
    s.onerror = () => reject(new Live2DUnavailable(t('还没装 Live2D 运行库：在 aila-desktop 里运行 npm run setup:live2d（会先给你看 Live2D 的许可协议）')))
    document.head.appendChild(s)
  })
  if (!window.Live2DCubismCore) throw new Live2DUnavailable(t('Live2D 运行库加载失败'))
}

/** 主进程读来的文件 → 带相对路径的 File（pixi-live2d-display 的 FileLoader 按 webkitRelativePath 找） */
export function toFiles(model: ModelFiles): File[] {
  return model.files.map((f) => {
    const file = new File([f.data], f.path.split('/').pop() ?? f.path)
    Object.defineProperty(file, 'webkitRelativePath', { value: f.path })
    return file
  })
}

export async function loadLive2DAvatar(model: ModelFiles): Promise<Live2DAvatar> {
  await loadCore()
  const PIXI = await import('pixi.js')
  // 页面的 CSP 不许 eval（安全规则）；PixiJS 6 默认用 eval 拼着色器同步代码，换成不用 eval 的实现
  const { install } = await import('@pixi/unsafe-eval')
  install(PIXI as never)
  const { Live2DModel } = await import('pixi-live2d-display/cubism4')
  const l2d = await Live2DModel.from(toFiles(model) as never, { autoInteract: false, autoUpdate: false })
  return new Live2DAvatar(PIXI, l2d)
}

type Pixi = typeof import('pixi.js')
type L2DModel = Awaited<ReturnType<typeof import('pixi-live2d-display/cubism4')['Live2DModel']['from']>>

export class Live2DAvatar implements Avatar {
  readonly root = new Object3D()
  private renderer: InstanceType<Pixi['Renderer']>
  private texture: CanvasTexture
  private mesh: Mesh
  private width: number
  private params: Record<string, number> = {}
  private head = new Vector3()

  constructor(
    PIXI: Pixi,
    private model: L2DModel
  ) {
    // 模型原始尺寸 → 离屏画布：高固定，宽按比例
    const ratio = model.width / model.height
    const h = CANVAS_HEIGHT
    const w = Math.max(64, Math.round(h * ratio))
    this.renderer = new PIXI.Renderer({ width: w, height: h, backgroundAlpha: 0, antialias: true, preserveDrawingBuffer: true })
    model.scale.set(h / model.height)
    model.position.set(0, 0)

    // 眨眼由我们的行为系统控制（和 VRM 一样的节奏）；模型自带的自动眨眼关掉
    const internal = model.internalModel as unknown as { eyeBlink?: unknown; on: (e: string, cb: () => void) => void; coreModel: { setParameterValueById: (id: string, v: number) => void } }
    internal.eyeBlink = undefined
    // 模型自己的动作（待机动作）更新完以后再写我们的参数：物理（头发、衣服）会跟着我们的头动
    internal.on('afterMotionUpdate', () => {
      for (const [id, v] of Object.entries(this.params)) {
        if (!SKIP.has(id)) internal.coreModel.setParameterValueById(id, v)
      }
    })

    this.texture = new CanvasTexture(this.renderer.view as HTMLCanvasElement)
    this.texture.colorSpace = SRGBColorSpace
    this.width = WORLD_HEIGHT * ratio
    const material = new MeshBasicMaterial({ map: this.texture, transparent: true, alphaTest: 0.02, depthWrite: false })
    this.mesh = new Mesh(new PlaneGeometry(this.width, WORLD_HEIGHT), material)
    this.mesh.position.set(0, WORLD_HEIGHT / 2, 0) // 脚在原点，和 VRM 一样
    this.root.add(this.mesh)
  }

  update(dt: number, pose: PoseState, expr: Expressions): void {
    const head = this.getHeadWorldPosition(this.head)
    const look = lookFrom(pose.lookTarget.x - head.x, pose.lookTarget.y - head.y, pose.lookTarget.z - head.z)
    this.params = live2dParams(pose, expr, look)
    this.model.update(dt * 1000)
    this.renderer.render(this.model as never)
    this.texture.needsUpdate = true
  }

  getNeckWorldPosition(target: Vector3): Vector3 {
    return target.set(0, WORLD_HEIGHT * (1 - NECK_FROM_TOP), 0)
  }

  getHeadWorldPosition(target: Vector3): Vector3 {
    return target.set(0, WORLD_HEIGHT * (1 - HEAD_FROM_TOP), 0)
  }

  dispose(): void {
    this.model.destroy()
    this.renderer.destroy()
    this.texture.dispose()
    this.mesh.geometry.dispose()
    ;(this.mesh.material as MeshBasicMaterial).dispose()
  }
}
