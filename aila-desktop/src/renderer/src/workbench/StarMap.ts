/**
 * 代码星图：三层——星系（子项目）→ 星座（目录 / 模块）→ 星球（文件）。
 *
 * - 远近两种看法：看全部时每个模块是一颗带名字的大星，模块之间是汇总后的光线，看得出代码库的结构；
 *   拉近以后大星淡去，星座散开成一个个文件，文件之间的调用是光线（光点沿调用方向流动）。中间平滑过渡
 * - 每个模块外面罩一层淡淡的星云，名字写在星云上；子项目的名字写在星系上方
 * - 跨进程的光线画成弧线：淡金是 HTTP 接口（桌面端 → AI 服务、桌面端 → 云端），淡青是 IPC 消息（预加载 → 主进程）。
 *   函数调用只在同一类语言里连得上，这几条弧线把前后端连起来
 * - 分层显示（layout.ts 的 layerAlpha）：看全部时只有模块和模块之间的线，拉近才出现文件和文件之间的线；
 *   光晕、星云拉近时淡下去，不会叠成一片紫雾；跨进程的弧线一直很淡，选中、报错时才亮
 * - 关联高亮：指着或点中一个文件，它和直接相连的文件亮起来、写出名字，其余暗下去；线分方向上色
 *   （青色：它用到的，琥珀色：用到它的）。看全部时指着一个模块，和它相连的模块也这样亮
 * - 第三方库是配角：外圈很淡的小点，不写名字；悬停、选中、选中的文件用到它时才亮、才有名字
 * - 测试文件调暗（也可以在界面上隐藏）
 * - 名字防重叠：每隔几帧按优先级重新摆一遍，挡住的先不显示
 * - 布局：d3-force-3d 力导向。光线把相关的文件拉近，星球之间互相排斥；每个文件被拉向自己模块的中心，
 *   模块的中心排在子项目的「星系」周围
 * - 画：Three.js。星球 = 球体 + 叠加发光的光晕；光线、光点各一批（一次绘制）；最后过一遍泛光（UnrealBloom）
 * - 报错：出错的星球变红、一跳一跳的，调用链上的变橙，它们之间的光线变亮，其余的暗下去
 *
 * 只用项目里已有的 three（和 VRM 角色同一份），不引入另一份 three。
 */
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { CSS2DObject, CSS2DRenderer } from 'three/examples/jsm/renderers/CSS2DRenderer.js'
import { EffectComposer } from 'three/examples/jsm/postprocessing/EffectComposer.js'
import { OutputPass } from 'three/examples/jsm/postprocessing/OutputPass.js'
import { RenderPass } from 'three/examples/jsm/postprocessing/RenderPass.js'
import { UnrealBloomPass } from 'three/examples/jsm/postprocessing/UnrealBloomPass.js'
import { forceCollide, forceLink, forceManyBody, forceSimulation, forceX, forceY, forceZ, type Simulation } from 'd3-force-3d'
import type { CodeGraph, GraphLink, GraphNode, LibInfo, LinkKind } from '../../../shared/workbench'
import {
  CHAIN_COLOR,
  HTTP_COLOR,
  IPC_COLOR,
  READ_COLOR,
  TOUCH_COLOR,
  ERROR_COLOR,
  buildModules,
  groupAnchors,
  groupColors,
  haloScale,
  isRelated,
  kindOf,
  layerAlpha,
  relatedFiles,
  relatedModules,
  USED_BY_COLOR,
  USES_COLOR,
  labelled,
  linkKey,
  moduleAnchors,
  moduleOf,
  moduleRadius,
  nodeRadius,
  placeLabels,
  textWidth,
  zoomLevel,
  type Highlight,
  type LabelBox,
  type Related,
  type ModuleInfo,
  type ModuleLink,
  type Vec3
} from './layout'

interface Star extends GraphNode {
  x?: number
  y?: number
  z?: number
  radius: number
  color: THREE.Color
  /** 按子项目的颜色（换上色方式时 color 会变，换回来用它） */
  base: THREE.Color
  mod: string
  anchor: Vec3
  /** 画在哪：拉远时往模块中心收拢（力导向算的是 x、y、z，这里是画出来的位置） */
  at: THREE.Vector3
}

interface Ray {
  source: Star | string
  target: Star | string
  weight: number
  key: string
  kind: LinkKind
}

interface Planet {
  star: Star
  mesh: THREE.Mesh<THREE.SphereGeometry, THREE.MeshBasicMaterial>
  halo: THREE.Sprite
  label: CSS2DObject | null
  /** 报错、调用链、选中、改过、悬停：拉远时也不淡下去 */
  lit: boolean
  /** 按选中、报错算出来的透明度；拉远时再乘上淡出的比例 */
  base: { mesh: number; halo: number }
  /** 拉远时也不往模块中心收（报错、选中、改过的，和选中文件的关联）：指着时的关联只亮不挪，免得鼠标一扫星图就跳 */
  pinned: boolean
}

/** 一个模块：拉远时是一颗大星，拉近时只剩一层星云和名字 */
interface Cluster {
  info: ModuleInfo
  color: THREE.Color
  base: THREE.Color
  stars: Star[]
  center: THREE.Vector3
  extent: number
  radius: number
  sun: THREE.Mesh<THREE.SphereGeometry, THREE.MeshBasicMaterial>
  halo: THREE.Sprite
  nebula: THREE.Sprite
  label: CSS2DObject
}

interface Galaxy {
  group: string
  stars: Star[]
  label: CSS2DObject
}

/** 一个第三方库：外圈的恒星，朝着用它的星系，连到用它的模块 */
interface LibStar {
  lib: LibInfo
  color: THREE.Color
  radius: number
  mesh: THREE.Mesh<THREE.SphereGeometry, THREE.MeshBasicMaterial>
  halo: THREE.Sprite
  label: CSS2DObject
  /** 用它的文件所在的模块 */
  modules: string[]
  at: THREE.Vector3
}

/** 恒星的颜色按生态分：npm 暖橙、Python 浅蓝、Java 浅粉（都很淡，和子项目的颜色分得开） */
const LIB_COLORS: Record<string, string> = { npm: '#ffd6a5', python: '#bde0fe', java: '#ffc8dd' }

const BACKGROUND = 0x04060d
const DIM = 0.22 // 有报错高亮时，其余的星球暗到多少
const TEST_DIM = 0.5 // 测试文件暗到多少
const FLY_MS = 900
const FLASH_MS = 1200
const COLLAPSE = 0.72 // 拉到最远时，文件往模块中心收拢多少
const BRIDGE_SEGMENTS = 18 // 一条弧线分几段画
const LABEL_EVERY = 4 // 每隔几帧重新摆一次名字
const SWAY = 0.32 // 没人动时左右晃多少（弧度，约 18°）

export class StarMap {
  /** 点了星球（点空白处是 null） */
  onSelect: (node: GraphNode | null) => void = () => {}
  /** 鼠标移到星球上（移开是 null）；坐标是相对画布的 */
  onHover: (node: GraphNode | null, x: number, y: number) => void = () => {}
  /** 鼠标移到模块的大星上（移开是 null） */
  onHoverModule: (m: ModuleInfo | null, x: number, y: number) => void = () => {}
  /** 点了外圈的第三方库（点别处是 null） */
  onSelectLib: (lib: LibInfo | null) => void = () => {}
  /** 鼠标移到第三方库上（移开是 null） */
  onHoverLib: (lib: LibInfo | null, x: number, y: number) => void = () => {}

  private renderer: THREE.WebGLRenderer
  /** 名字用 HTML 叠在上面：字清楚，也不会被泛光糊掉 */
  private labels = new CSS2DRenderer()
  private scene = new THREE.Scene()
  private camera: THREE.PerspectiveCamera
  private controls: OrbitControls
  private composer: EffectComposer
  private bloom: UnrealBloomPass
  private glow = makeGlowTexture()
  private sphere = new THREE.SphereGeometry(1, 28, 18)
  private planets = new Map<string, Planet>()
  private clusters = new Map<string, Cluster>()
  private galaxies: Galaxy[] = []
  private stars: Star[] = []
  /** 函数调用（文件之间） */
  private rays: Ray[] = []
  /** 跨进程：HTTP、IPC（画成弧线） */
  private bridges: Ray[] = []
  private moduleRays: ModuleLink[] = []
  private lines: THREE.LineSegments | null = null
  private sparks: THREE.Points | null = null
  private sparkPhase: Float32Array = new Float32Array(0)
  private arcs: THREE.LineSegments | null = null
  private arcSparks: THREE.Points | null = null
  private arcPhase: Float32Array = new Float32Array(0)
  private moduleLines: THREE.LineSegments | null = null
  /** import（项目里文件之间）：参与布局，拉远时并进模块之间的光线 */
  private imports: Ray[] = []
  /** 画成直线的：函数调用 + import（import 平时不显示，关联高亮时才亮） */
  private drawn: Ray[] = []
  /** 项目里文件之间的全部关系：算关联用 */
  private graphLinks: Pick<GraphLink, 'source' | 'target' | 'kind'>[] = []
  /** 指着或点中的文件和它直接相连的文件（有报错高亮时不算） */
  private related: Related | null = null
  /** 看全部时指着的模块和与它相连的模块 */
  private relatedMods: { focus: string; out: Set<string>; in: Set<string> } | null = null
  private libs: LibStar[] = []
  /** 第三方库连到用它的模块的线 */
  private libLines: THREE.LineSegments | null = null
  private hoveredLib: string | null = null
  private selectedLib: string | null = null
  private showLibs = true
  private center = new THREE.Vector3()
  private sim: Simulation<Star> | null = null
  private highlight: Highlight | null = null
  private selected: string | null = null
  private hovered: string | null = null
  private hoveredModule: string | null = null
  private showLabels = new Set<string>()
  private fly: { from: THREE.Vector3; to: THREE.Vector3; aimFrom: THREE.Vector3; aimTo: THREE.Vector3; start: number } | null = null
  private raycaster = new THREE.Raycaster()
  private pointer = new THREE.Vector2()
  private pressed: { x: number; y: number } | null = null
  private clock = new THREE.Clock()
  private frame = 0
  private frames = 0
  /** 0 = 近（看文件），1 = 远（看模块） */
  private zoom = 1
  /** 「看全部」时镜头离中心多远：远近按它来算 */
  private fitDistance = 260
  /** 用户自己转过、缩放过镜头：布局稳定后就不再自动取景 */
  private userMoved = false
  /** 自己左右晃：从哪个方位角、哪个时刻开始晃（用户动了镜头就是 null） */
  private sway: { theta: number; start: number } | null = null
  /** 正在放的特效（法阵、光环） */
  private effects: { obj: THREE.Object3D; start: number; ms: number; step: (obj: THREE.Object3D, f: number) => number }[] = []
  /** 工作窗口里她这次改过的文件（变绿、显示名字） */
  private touched = new Set<string>()
  /** 星球上闪一下的光环（她在读、刚改了这个文件） */
  private flashes: { sprite: THREE.Sprite; start: number; radius: number }[] = []
  private resize: ResizeObserver
  /** 屏幕上不能压的地方（工具栏、图例、面板，相对画布的像素）：名字摆在上面就不显示 */
  private reserved: LabelBox[] = []

  constructor(private el: HTMLElement) {
    this.renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: 'high-performance' })
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    // 背景色设在场景上（会按色彩空间换算）；用 setClearColor 的话，最后输出时又转一次 sRGB，深色会发灰
    this.scene.background = new THREE.Color(BACKGROUND)
    el.appendChild(this.renderer.domElement)
    Object.assign(this.labels.domElement.style, { position: 'absolute', top: '0', left: '0', pointerEvents: 'none' })
    el.appendChild(this.labels.domElement)

    this.camera = new THREE.PerspectiveCamera(55, 1, 0.5, 5000)
    this.camera.position.set(0, 60, 260)
    this.controls = new OrbitControls(this.camera, this.renderer.domElement)
    this.controls.enableDamping = true
    this.controls.dampingFactor = 0.08
    this.controls.addEventListener('start', () => {
      this.sway = null // 用户动手了就不自己晃了
      this.fly = null
      this.userMoved = true
    })

    this.composer = new EffectComposer(this.renderer)
    this.composer.addPass(new RenderPass(this.scene, this.camera))
    // 只让亮的东西（星球、光点）发光：门槛太低整个画面会发灰
    // 门槛提高、强度压低：只有真的亮的（选中、报错、光点）才泛光；以前门槛低，拉近时整个画面过曝
    this.bloom = new UnrealBloomPass(new THREE.Vector2(1, 1), 0.45, 0.3, 0.6)
    this.composer.addPass(this.bloom)
    this.composer.addPass(new OutputPass())

    this.scene.add(makeStarfield())

    const canvas = this.renderer.domElement
    canvas.addEventListener('pointerdown', this.onPointerDown)
    canvas.addEventListener('pointerup', this.onPointerUp)
    canvas.addEventListener('pointermove', this.onPointerMove)
    canvas.addEventListener('pointerleave', () => this.setHovered(null, 0, 0))
    this.resize = new ResizeObserver(() => this.fit())
    this.resize.observe(el)
    this.fit()
    this.frame = requestAnimationFrame(this.loop)
  }

  // ---------- 数据 ----------

  setGraph(graph: CodeGraph): void {
    this.clear()
    const colors = groupColors(graph.nodes)
    const groups = [...colors.keys()]
    const sizes = groups.map((g) => graph.nodes.filter((n) => n.group === g).length)
    const anchors = groupAnchors(groups, sizes)
    const { modules, links: moduleLinks } = buildModules(graph)
    const placed = moduleAnchors(modules, anchors)
    this.showLabels = labelled(graph.nodes, 40)

    this.stars = graph.nodes.map((n) => {
      const mod = moduleOf(n)
      const anchor = placed.get(mod) ?? anchors.get(n.group) ?? [0, 0, 0]
      const base = new THREE.Color(colors.get(n.group) ?? '#8fa8ff')
      // 没提交的改动亮一点（往白色混一些）；测试文件往灰色混
      let color = n.modified ? base.clone().lerp(new THREE.Color('#ffffff'), 0.28) : base
      if (n.test) color = color.clone().lerp(new THREE.Color('#5d6782'), 0.55)
      return {
        ...n,
        mod,
        anchor,
        radius: nodeRadius(n.lines) * (n.test ? 0.8 : 1),
        color,
        base: color.clone(),
        at: new THREE.Vector3(),
        x: anchor[0] + (Math.random() - 0.5) * 20,
        y: anchor[1] + (Math.random() - 0.5) * 20,
        z: anchor[2] + (Math.random() - 0.5) * 20
      }
    })
    const known = new Set(this.stars.map((s) => s.id))
    const kept = graph.links.filter((l) => known.has(l.source) && known.has(l.target))
    const toRay = (l: (typeof kept)[number]): Ray => ({
      source: l.source,
      target: l.target,
      weight: l.weight,
      key: linkKey(l),
      kind: kindOf(l)
    })
    this.rays = kept.filter((l) => kindOf(l) === 'call').map(toRay)
    this.bridges = kept.filter((l) => kindOf(l) === 'http' || kindOf(l) === 'ipc').map(toRay)
    this.imports = kept.filter((l) => kindOf(l) === 'import').map(toRay)
    this.drawn = [...this.rays, ...this.imports]
    this.graphLinks = kept
    // 模块之间：调用和 import 合成一条（同一对模块只画一条）
    const merged = new Map<string, ModuleLink>()
    for (const l of moduleLinks) {
      if (l.kind !== 'call' && l.kind !== 'import') continue
      const key = `${l.source}→${l.target}`
      const cur = merged.get(key) ?? { source: l.source, target: l.target, kind: 'call' as const, weight: 0 }
      cur.weight += l.weight
      merged.set(key, cur)
    }
    this.moduleRays = [...merged.values()]

    for (const star of this.stars) this.addPlanet(star)
    for (const m of modules) this.addCluster(m, new THREE.Color(colors.get(m.group) ?? '#8fa8ff'))
    for (const g of groups) this.addGalaxy(g, colors.get(g) ?? '#8fa8ff')
    for (const lib of graph.libs ?? []) this.addLib(lib)
    this.buildRays()

    // 弧线也参与布局：拉得比函数调用松一些（前后端本来就在两个星系里）；import 和调用一样拉近
    const pulls = [...this.rays, ...this.imports, ...this.bridges]
    this.sim = forceSimulation<Star>(this.stars, 3)
      .force(
        'link',
        forceLink<Star, Ray>(pulls)
          .id((s) => s.id)
          .distance((r) => (r.kind === 'call' ? 16 + 26 / Math.sqrt(r.weight) : r.kind === 'import' ? 30 : 120))
          .strength((r) => (r.kind === 'call' ? 0.22 : r.kind === 'import' ? 0.08 : 0.01))
      )
      .force('charge', forceManyBody<Star>().strength(-34).distanceMax(160))
      .force('collide', forceCollide<Star>((s) => s.radius + 1.5).strength(0.8))
      .force('x', forceX<Star>((s) => s.anchor[0]).strength(0.09))
      .force('y', forceY<Star>((s) => s.anchor[1]).strength(0.09))
      .force('z', forceZ<Star>((s) => s.anchor[2]).strength(0.09))
      .stop()
    this.sim.tick(300) // 先在后台排好大半，一打开就是差不多的样子；剩下的几秒慢慢稳定
    this.sync()
    this.userMoved = false
    this.frameAll()
  }

  setHighlight(h: Highlight | null): void {
    this.highlight = h
    this.applyStyles()
  }

  /** 选中一颗星球（null 取消），并把镜头飞过去 */
  select(id: string | null, fly = true): void {
    this.selected = id
    if (id) this.selectedLib = null
    this.applyStyles()
    if (id && fly) this.flyTo(id)
  }

  // ---------- 她在干活 ----------

  /**
   * 工作窗口里她碰了这个文件：读 → 星球上闪一圈青色；改 → 闪一圈绿色，之后一直是绿的、显示名字。
   * 返回这颗星球在不在图上
   */
  touch(id: string, kind: 'read' | 'edit'): boolean {
    const p = this.planets.get(id)
    if (!p) return false
    if (kind === 'edit' && !this.touched.has(id)) {
      this.touched.add(id)
      this.applyStyles()
    }
    const sprite = new THREE.Sprite(
      new THREE.SpriteMaterial({
        map: this.glow,
        color: kind === 'edit' ? TOUCH_COLOR : READ_COLOR,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
        transparent: true
      })
    )
    sprite.position.copy(p.mesh.position)
    this.scene.add(sprite)
    this.flashes.push({ sprite, start: performance.now(), radius: p.star.radius })
    return true
  }

  /** 换了项目或者开了新任务：不再标改过的文件 */
  clearTouched(): void {
    this.touched.clear()
    this.applyStyles()
  }

  /**
   * 换上色方式：files、modules 是 id → 颜色；null 换回按子项目。光线跟着出发的星球变色
   */
  setColors(files: Map<string, string> | null, modules: Map<string, string> | null): void {
    for (const s of this.stars) s.color = files?.has(s.id) ? new THREE.Color(files.get(s.id)!) : s.base.clone()
    for (const c of this.clusters.values()) {
      const hex = modules?.get(c.info.id)
      c.color = hex ? new THREE.Color(hex) : c.base.clone()
      for (const m of [c.sun.material, c.halo.material, c.nebula.material]) m.color.copy(c.color)
      const css = `#${c.color.getHexString()}`
      c.label.element.style.color = css
      c.label.element.style.textShadow = `0 0 8px ${css}, 0 0 2px #000`
    }
    this.applyStyles()
  }

  /** 外圈的第三方库：显示还是藏起来 */
  setShowLibs(on: boolean): void {
    this.showLibs = on
    for (const l of this.libs) l.mesh.visible = l.halo.visible = on
    if (this.libLines) this.libLines.visible = on
    if (!on) this.selectedLib = null
    this.applyStyles()
  }

  /** 回放时拖动时间轴：直接设定到这一步为止改过的文件（不闪） */
  setTouched(ids: Iterable<string>): void {
    this.touched = new Set([...ids].filter((id) => this.planets.has(id)))
    this.applyStyles()
  }

  /** 回放时镜头跟着走：平移到这颗星球，不拉近拉远（一步一缩放会晕） */
  follow(id: string): void {
    const p = this.planets.get(id)
    if (!p) return
    const s = p.star
    this.flyToPoint(new THREE.Vector3(s.x ?? 0, s.y ?? 0, s.z ?? 0), this.camera.position.distanceTo(this.controls.target))
  }

  private runFlashes(): void {
    const now = performance.now()
    this.flashes = this.flashes.filter((f) => {
      const t = Math.min(1, (now - f.start) / FLASH_MS)
      f.sprite.scale.setScalar(f.radius * (3 + 12 * t))
      f.sprite.material.opacity = 1 - t
      if (t < 1) return true
      this.scene.remove(f.sprite)
      f.sprite.material.dispose()
      return false
    })
  }

  // ---------- 手势 ----------

  /** 绕着中心转镜头；dx、dy 是窗口比例（手移过整个窗口宽度 ≈ 转一圈） */
  orbit(dx: number, dy: number): void {
    this.takeOver()
    const offset = this.camera.position.clone().sub(this.controls.target)
    const s = new THREE.Spherical().setFromVector3(offset)
    s.theta -= dx * Math.PI * 2
    s.phi = THREE.MathUtils.clamp(s.phi - dy * Math.PI * 2, 0.15, Math.PI - 0.15)
    this.camera.position.copy(this.controls.target).add(new THREE.Vector3().setFromSpherical(s))
  }

  /** 镜头拉近（factor < 1）或拉远（> 1） */
  dolly(factor: number): void {
    this.takeOver()
    const offset = this.camera.position.clone().sub(this.controls.target)
    const len = THREE.MathUtils.clamp(offset.length() * THREE.MathUtils.clamp(factor, 0.8, 1.25), 15, 3000)
    this.camera.position.copy(this.controls.target).add(offset.setLength(len))
  }

  /** 窗口比例坐标下的星球（手势光标用） */
  pickAt(x: number, y: number): GraphNode | null {
    const rect = this.renderer.domElement.getBoundingClientRect()
    const hit = this.pick(rect.left + x * rect.width, rect.top + y * rect.height)
    return hit && 'star' in hit ? hit.star : null
  }

  /** 手势光标移到星球上：和鼠标悬停一样显示名字 */
  hoverAt(p: { x: number; y: number } | null): void {
    if (!p) return this.setHovered(null, 0, 0)
    const rect = this.renderer.domElement.getBoundingClientRect()
    const hit = this.pick(rect.left + p.x * rect.width, rect.top + p.y * rect.height)
    this.setHovered(hit, p.x * rect.width, p.y * rect.height)
  }

  /** 唤出：镜头中心展开一个旋转的法阵，然后淡掉 */
  summonEffect(): void {
    this.addEffect(makeSigil(MAGIC_COLOR), 1600, (obj, f) => {
      const grow = 1 - Math.pow(1 - Math.min(1, f / 0.35), 3)
      obj.scale.setScalar(0.15 + 0.85 * grow)
      obj.children.forEach((c, i) => (c.rotation.z = (i % 2 ? -1 : 1) * f * (1.2 + i * 0.4)))
      return f < 0.15 ? f / 0.15 : f > 0.7 ? (1 - f) / 0.3 : 1
    })
  }

  /** 结印：一圈金色的光环往外扩散 */
  sealEffect(): void {
    this.addEffect(makeRings(SEAL_COLOR), 900, (obj, f) => {
      obj.scale.setScalar(0.2 + f * 1.1)
      return 1 - f
    })
  }

  /** 镜头中间一个对着镜头的平面特效：step 设大小、转动，返回透明度；时间到了就移除 */
  private addEffect(obj: THREE.Object3D, ms: number, step: (obj: THREE.Object3D, f: number) => number): void {
    this.scene.add(obj)
    this.effects.push({ obj, start: performance.now(), ms, step })
  }

  private runEffects(): void {
    const now = performance.now()
    this.effects = this.effects.filter((e) => {
      const f = Math.min(1, (now - e.start) / e.ms)
      // 摆在镜头前方、正对镜头，大小跟着镜头距离走：看起来总是差不多大
      const dist = this.camera.position.distanceTo(this.controls.target)
      e.obj.position.copy(this.controls.target)
      e.obj.quaternion.copy(this.camera.quaternion)
      const opacity = e.step(e.obj, f)
      e.obj.scale.multiplyScalar(dist * 0.32)
      e.obj.traverse((o) => {
        const m = (o as THREE.Line).material as THREE.LineBasicMaterial | undefined
        if (m) m.opacity = opacity
      })
      if (f < 1) return true
      this.scene.remove(e.obj)
      disposeTree(e.obj)
      return false
    })
  }

  private takeOver(): void {
    this.sway = null
    this.fly = null
    this.userMoved = true
  }

  /** 镜头拉远，看整个星图（这时看到的是模块） */
  frameAll(): void {
    const box = new THREE.Box3()
    for (const s of this.stars) box.expandByPoint(new THREE.Vector3(s.x ?? 0, s.y ?? 0, s.z ?? 0))
    if (this.showLibs) for (const l of this.libs) box.expandByPoint(l.at) // 外圈的第三方库也要在画面里
    if (box.isEmpty()) return
    const center = box.getCenter(new THREE.Vector3())
    const size = box.getSize(new THREE.Vector3()).length()
    const offset = new THREE.Vector3(0, size * 0.18, size * 0.62 + 50)
    this.fly = null
    this.camera.position.copy(center).add(offset)
    this.controls.target.copy(center)
    this.controls.update()
    this.fitDistance = offset.length()
    if (!this.userMoved) this.sway = { theta: new THREE.Spherical().setFromVector3(offset).theta, start: this.clock.elapsedTime }
  }

  dispose(): void {
    cancelAnimationFrame(this.frame)
    this.resize.disconnect()
    this.clear()
    for (const e of this.effects) disposeTree(e.obj)
    this.effects = []
    this.controls.dispose()
    this.composer.dispose()
    this.sphere.dispose()
    this.glow.dispose()
    this.renderer.dispose()
    this.renderer.domElement.remove()
    this.labels.domElement.remove()
  }

  // ---------- 搭场景 ----------

  private clear(): void {
    this.sim?.stop()
    this.sim = null
    const dropLabel = (label: CSS2DObject | null): void => {
      if (!label) return
      this.scene.remove(label)
      label.element.remove()
    }
    for (const p of this.planets.values()) {
      this.scene.remove(p.mesh, p.halo)
      p.mesh.material.dispose()
      p.halo.material.dispose()
      dropLabel(p.label)
    }
    this.planets.clear()
    for (const c of this.clusters.values()) {
      this.scene.remove(c.sun, c.halo, c.nebula)
      c.sun.material.dispose()
      c.halo.material.dispose()
      c.nebula.material.dispose()
      dropLabel(c.label)
    }
    this.clusters.clear()
    for (const g of this.galaxies) dropLabel(g.label)
    this.galaxies = []
    for (const l of this.libs) {
      this.scene.remove(l.mesh, l.halo)
      l.mesh.material.dispose()
      l.halo.material.dispose()
      dropLabel(l.label)
    }
    this.libs = []
    this.selectedLib = this.hoveredLib = null
    for (const obj of [this.lines, this.sparks, this.arcs, this.arcSparks, this.moduleLines, this.libLines]) {
      if (!obj) continue
      this.scene.remove(obj)
      obj.geometry.dispose()
      ;(obj.material as THREE.Material).dispose()
    }
    this.lines = this.sparks = this.arcs = this.arcSparks = this.moduleLines = this.libLines = null
    this.selected = this.hovered = this.hoveredModule = null
    this.touched.clear()
    for (const f of this.flashes) {
      this.scene.remove(f.sprite)
      f.sprite.material.dispose()
    }
    this.flashes = []
    this.highlight = null
  }

  private addPlanet(star: Star): void {
    const mesh = new THREE.Mesh(this.sphere, new THREE.MeshBasicMaterial({ color: star.color, transparent: true }))
    mesh.scale.setScalar(star.radius)
    mesh.userData.id = star.id
    const halo = this.glowSprite(star.color, 0.55)
    halo.scale.setScalar(star.radius * haloScale(1))
    this.scene.add(mesh, halo)
    this.planets.set(star.id, { star, mesh, halo, label: null, lit: false, pinned: false, base: { mesh: 1, halo: 0.55 } })
  }

  private addCluster(info: ModuleInfo, groupColor: THREE.Color): void {
    const color = info.test ? groupColor.clone().lerp(new THREE.Color('#5d6782'), 0.55) : groupColor.clone()
    const radius = moduleRadius(info.lines) * (info.test ? 0.8 : 1)
    const sun = new THREE.Mesh(this.sphere, new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0 }))
    sun.userData.module = info.id
    sun.scale.setScalar(radius)
    const halo = this.glowSprite(color, 0)
    halo.scale.setScalar(radius * 3.6)
    // 星云：只是一层很淡的光，不参与泛光（亮度低于泛光门槛）
    const nebula = this.glowSprite(color, 0.07)
    nebula.renderOrder = -1
    const label = makeModuleLabel(info.label, `#${color.getHexString()}`)
    this.scene.add(nebula, sun, halo, label)
    const stars = this.stars.filter((s) => s.mod === info.id)
    this.clusters.set(info.id, { info, color, base: color.clone(), stars, center: new THREE.Vector3(), extent: 10, radius, sun, halo, nebula, label })
  }

  private addGalaxy(group: string, color: string): void {
    const label = makeGalaxyLabel(group, color)
    this.scene.add(label)
    this.galaxies.push({ group, stars: this.stars.filter((s) => s.group === group), label })
  }

  private addLib(lib: LibInfo): void {
    const color = new THREE.Color(LIB_COLORS[lib.ecosystem] ?? '#fff1c4')
    const radius = 1.4 + Math.log2(lib.count + 1) * 0.45 // 配角：比模块小得多
    const mesh = new THREE.Mesh(this.sphere, new THREE.MeshBasicMaterial({ color, transparent: true }))
    mesh.scale.setScalar(radius)
    mesh.userData.lib = lib.id
    const halo = this.glowSprite(color, 0.18)
    halo.scale.setScalar(radius * 4)
    const label = makeModuleLabel(lib.name, `#${color.getHexString()}`)
    label.element.classList.add('lib-label')
    const owners = new Map(this.stars.map((s) => [s.id, s.mod]))
    const modules = [...new Set(lib.users.map((u) => owners.get(u)).filter((m): m is string => !!m))]
    this.scene.add(mesh, halo, label)
    mesh.visible = halo.visible = this.showLibs
    this.libs.push({ lib, color, radius, mesh, halo, label, modules, at: new THREE.Vector3() })
  }

  private glowSprite(color: THREE.Color, opacity: number): THREE.Sprite {
    return new THREE.Sprite(
      new THREE.SpriteMaterial({
        map: this.glow,
        color,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
        transparent: true,
        opacity
      })
    )
  }

  private buildRays(): void {
    const additive = { transparent: true, blending: THREE.AdditiveBlending, depthWrite: false } as const
    // 函数调用：一条一段
    this.lines = new THREE.LineSegments(
      lineGeometry(this.drawn.length * 2),
      new THREE.LineBasicMaterial({ vertexColors: true, opacity: 0.5, ...additive })
    )
    // 光点：每条光线一个，调用多的两个，沿调用方向流动
    const built = sparkGeometry(this.rays.map((r) => (r.weight > 3 ? 2 : 1)))
    this.sparkPhase = built.phase
    this.sparks = new THREE.Points(
      built.geometry,
      new THREE.PointsMaterial({ size: 2.2, map: this.glow, vertexColors: true, ...additive })
    )
    // 跨进程：每条弧线分 BRIDGE_SEGMENTS 段，光点大一些
    this.arcs = new THREE.LineSegments(
      lineGeometry(this.bridges.length * BRIDGE_SEGMENTS * 2),
      new THREE.LineBasicMaterial({ vertexColors: true, opacity: 0.8, ...additive })
    )
    const arcBuilt = sparkGeometry(this.bridges.map((r) => Math.min(4, 1 + r.weight)))
    this.arcPhase = arcBuilt.phase
    this.arcSparks = new THREE.Points(
      arcBuilt.geometry,
      new THREE.PointsMaterial({ size: 3.6, map: this.glow, vertexColors: true, ...additive })
    )
    // 模块之间（拉远时）
    this.moduleLines = new THREE.LineSegments(
      lineGeometry(this.moduleRays.length * 2),
      new THREE.LineBasicMaterial({ vertexColors: true, opacity: 0, ...additive })
    )
    // 第三方库连到用它的模块
    this.libLines = new THREE.LineSegments(
      lineGeometry(this.libs.reduce((n, l) => n + l.modules.length, 0) * 2),
      new THREE.LineBasicMaterial({ vertexColors: true, opacity: 0.5, ...additive })
    )
    this.libLines.visible = this.showLibs
    this.scene.add(this.lines, this.sparks, this.arcs, this.arcSparks, this.moduleLines, this.libLines)
    this.applyStyles()
  }

  /** 按现在的选中、悬停、报错高亮，给星球、光线、光点上色；透明度再由远近（applyZoom）决定 */
  private applyStyles(): void {
    const h = this.highlight
    // 关联高亮：指着的优先，没指着就看点中的；有报错高亮时不算（报错那套颜色优先）
    const focusId = this.hovered ?? this.selected
    this.related = !h && focusId ? relatedFiles(this.graphLinks, focusId) : null
    this.relatedMods = !h && !this.related && this.hoveredModule ? relatedModules(this.moduleRays, this.hoveredModule) : null
    const rel = this.related
    const relMods = this.relatedMods
    const modOn = (mod: string): boolean => !relMods || mod === relMods.focus || relMods.out.has(mod) || relMods.in.has(mod)
    const special = (id: string): boolean =>
      !!h?.error.has(id) || !!h?.chain.has(id) || id === this.selected || this.touched.has(id) || id === this.hovered
    for (const p of this.planets.values()) {
      const id = p.star.id
      const color = h?.error.has(id)
        ? new THREE.Color(ERROR_COLOR)
        : h?.chain.has(id)
          ? new THREE.Color(CHAIN_COLOR)
          : this.touched.has(id)
            ? new THREE.Color(TOUCH_COLOR)
            : p.star.color
      p.mesh.material.color.copy(color)
      p.halo.material.color.copy(color)
      const inRel = !!rel && isRelated(rel, id)
      p.lit = special(id) || inRel
      // 点中的文件的关联拉远时也散开；只是指着的话只亮不挪
      p.pinned = special(id) && id !== this.hovered ? true : inRel && rel?.focus === this.selected
      const on = h ? p.lit : rel ? inRel : modOn(p.star.mod)
      const test = p.star.test && !p.lit ? TEST_DIM : 1
      // 关联高亮时无关的几乎看不见（只留个影子），一眼就看出谁和它有关
      p.base.mesh = (on ? 1 : rel ? 0.05 : DIM) * test
      p.base.halo = (on ? (id === this.selected || id === rel?.focus ? 0.95 : 0.55) : rel ? 0 : DIM * 0.6) * test
      if (!p.label && (p.lit || (!h && this.showLabels.has(id)))) {
        p.label = makeLabel(p.star.name)
        p.label.visible = false
        this.scene.add(p.label)
      }
      p.label?.element.classList.toggle('error', !!h?.error.has(id))
    }

    const near = (r: Ray): boolean => !!rel?.links.has(r.key)
    const dirColor = (r: Ray): THREE.Color | null => {
      const d = rel?.links.get(r.key)
      return d ? new THREE.Color(d === 'out' ? USES_COLOR : USED_BY_COLOR) : null
    }
    if (this.lines) {
      const colors = this.lines.geometry.getAttribute('color') as THREE.BufferAttribute
      this.drawn.forEach((r, i) => {
        const hot = h?.links.has(r.key)
        const src = this.planets.get(starId(r.source))
        const dir = dirColor(r)
        const c = hot ? new THREE.Color(ERROR_COLOR) : dir ?? (src?.star.color ?? new THREE.Color('#8fa8ff')).clone()
        // import 平时不画（只参与布局），关联高亮时和调用一样亮
        c.multiplyScalar(hot ? 1 : dir ? 1 : h || rel ? 0.04 : r.kind === 'import' ? 0 : src?.star.test ? 0.22 : 0.5)
        colors.setXYZ(i * 2, c.r, c.g, c.b)
        colors.setXYZ(i * 2 + 1, c.r, c.g, c.b)
      })
      colors.needsUpdate = true
    }
    if (this.sparks) {
      const colors = this.sparks.geometry.getAttribute('color') as THREE.BufferAttribute
      const owner = this.sparks.geometry.userData.owner as Uint32Array
      owner.forEach((i, k) => {
        const r = this.rays[i]
        const hot = h?.links.has(r.key)
        const src = this.planets.get(starId(r.source))
        const dir = dirColor(r)
        const c = hot ? new THREE.Color(CHAIN_COLOR) : dir ?? (src?.star.color ?? new THREE.Color('#ffffff')).clone()
        c.multiplyScalar(hot ? 1.2 : h ? 0.1 : dir ? 1.3 : rel ? 0.02 : src?.star.test ? 0.2 : 0.45)
        colors.setXYZ(k, c.r, c.g, c.b)
      })
      colors.needsUpdate = true
    }
    const bridgeColor = (r: Ray, boost: number): THREE.Color => {
      const test = this.planets.get(starId(r.source))?.star.test
      const k = near(r) ? 1.25 : h ? (h.links.has(r.key) ? 1 : 0.12) : rel ? 0.04 : test ? 0.1 : 0.5
      return new THREE.Color(r.kind === 'ipc' ? IPC_COLOR : HTTP_COLOR).multiplyScalar(k * boost)
    }
    if (this.arcs) {
      const colors = this.arcs.geometry.getAttribute('color') as THREE.BufferAttribute
      this.bridges.forEach((r, i) => {
        const c = bridgeColor(r, 1)
        for (let s = 0; s < BRIDGE_SEGMENTS * 2; s++) colors.setXYZ(i * BRIDGE_SEGMENTS * 2 + s, c.r, c.g, c.b)
      })
      colors.needsUpdate = true
    }
    if (this.arcSparks) {
      const colors = this.arcSparks.geometry.getAttribute('color') as THREE.BufferAttribute
      const owner = this.arcSparks.geometry.userData.owner as Uint32Array
      owner.forEach((i, k) => {
        const c = bridgeColor(this.bridges[i], 1.3)
        colors.setXYZ(k, c.r, c.g, c.b)
      })
      colors.needsUpdate = true
    }
    if (this.moduleLines) {
      const colors = this.moduleLines.geometry.getAttribute('color') as THREE.BufferAttribute
      this.moduleRays.forEach((r, i) => {
        const src = this.clusters.get(r.source)
        // 指着一个模块：连着它的线分方向亮（青：它用到的，琥珀：用到它的），别的几乎看不见
        const dir = relMods ? (r.source === relMods.focus ? USES_COLOR : r.target === relMods.focus ? USED_BY_COLOR : null) : null
        const c = dir ? new THREE.Color(dir) : (src?.color ?? new THREE.Color('#8fa8ff')).clone()
        const strength = Math.min(0.6, Math.log2(r.weight + 1) * 0.12)
        c.multiplyScalar(dir ? 0.7 + strength : relMods ? 0.04 : (h ? 0.15 : src?.info.test ? 0.25 : 0.4) + strength)
        colors.setXYZ(i * 2, c.r, c.g, c.b)
        colors.setXYZ(i * 2 + 1, c.r, c.g, c.b)
      })
      colors.needsUpdate = true
    }
    // 指着或点中的文件用到的库也亮
    const selectedLibs = new Set(this.libs.filter((l) => focusId && l.lib.users.includes(focusId)).map((l) => l.lib.id))
    for (const l of this.libs) {
      const hot = l.lib.id === this.selectedLib || l.lib.id === this.hoveredLib || selectedLibs.has(l.lib.id)
      l.mesh.material.opacity = h ? 0.2 : hot ? 1 : 0.45
      l.halo.material.opacity = h ? 0.06 : hot ? 0.8 : 0.16
    }
    if (this.libLines) {
      const colors = this.libLines.geometry.getAttribute('color') as THREE.BufferAttribute
      let k = 0
      for (const l of this.libs) {
        const hot = l.lib.id === this.selectedLib || l.lib.id === this.hoveredLib || selectedLibs.has(l.lib.id)
        const c = l.color.clone().multiplyScalar(h ? 0.03 : hot ? 0.8 : 0.035)
        for (let i = 0; i < l.modules.length * 2; i++) colors.setXYZ(k++, c.r, c.g, c.b)
      }
      colors.needsUpdate = true
    }
    this.applyZoom()
    if (this.sim) this.sync() // 选中、报错的文件拉远时也不收拢：位置马上跟着变
  }

  /** 远近：按 layerAlpha 分层（看全部时是模块，拉近是文件）；选中、报错时相关的东西不管远近都亮 */
  private applyZoom(): void {
    const t = this.zoom
    const L = layerAlpha(t)
    const focus = !!this.highlight || !!this.selected || !!this.related
    const hs = haloScale(t)
    for (const p of this.planets.values()) {
      p.mesh.material.opacity = p.lit ? p.base.mesh : p.base.mesh * L.planets
      p.halo.material.opacity = p.lit ? p.base.halo : p.base.halo * L.halos
      p.halo.userData.scale = hs
    }
    const h = this.highlight
    for (const c of this.clusters.values()) {
      const rm = this.relatedMods
      const rel = this.related
      const hot = h
        ? c.stars.some((s) => h.error.has(s.id) || h.chain.has(s.id))
        : rm
          ? c.info.id === rm.focus || rm.out.has(c.info.id) || rm.in.has(c.info.id)
          : rel
            ? c.stars.some((s) => isRelated(rel, s.id))
            : true
      const k = (hot ? 1 : rm || rel ? 0.18 : 0.3) * (c.info.test ? TEST_DIM : 1) * (c.info.id === this.hoveredModule ? 1.3 : 1)
      c.sun.material.opacity = Math.min(1, L.moduleSuns * k)
      c.halo.material.opacity = Math.min(1, L.moduleHalos * k)
      c.nebula.material.opacity = L.nebula * (hot ? 1 : 0.5) * (c.info.test ? 0.6 : 1)
      c.sun.visible = c.halo.visible = t > 0.02
      c.nebula.visible = L.nebula > 0.002
    }
    // 选中、报错时：文件之间的线、光点不管远近都要看得到（颜色里已经把无关的压暗了）
    if (this.lines) (this.lines.material as THREE.LineBasicMaterial).opacity = focus ? Math.max(0.55, L.fileLines) : L.fileLines
    if (this.sparks) (this.sparks.material as THREE.PointsMaterial).opacity = focus ? Math.max(0.8, L.sparks) : L.sparks
    if (this.lines) this.lines.visible = focus || L.fileLines > 0.005
    if (this.sparks) this.sparks.visible = focus || L.sparks > 0.005
    if (this.arcs) (this.arcs.material as THREE.LineBasicMaterial).opacity = focus ? 0.8 : L.arcs
    if (this.arcSparks) (this.arcSparks.material as THREE.PointsMaterial).opacity = focus ? 1 : L.arcs * 2
    if (this.moduleLines) (this.moduleLines.material as THREE.LineBasicMaterial).opacity = this.relatedMods ? Math.max(0.75, L.moduleLines) : L.moduleLines
  }

  // ---------- 每一帧 ----------

  private loop = (): void => {
    this.frame = requestAnimationFrame(this.loop)
    const dt = Math.min(this.clock.getDelta(), 0.05)
    const t = this.clock.elapsedTime
    let moved = false
    if (this.sim && this.sim.alpha() > this.sim.alphaMin()) {
      this.sim.tick()
      moved = true
      // 刚稳定下来：星图还在往外散的时候取的景不准，再取一次（用户动过镜头、正在飞向某颗星就不管）
      if (this.sim.alpha() <= this.sim.alphaMin() && !this.userMoved && !this.fly && !this.selected) this.frameAll()
    }
    this.flyStep()
    this.swayStep(t)
    this.controls.update()
    const zoom = zoomLevel(this.camera.position.distanceTo(this.controls.target), this.fitDistance)
    if (Math.abs(zoom - this.zoom) > 0.002) {
      this.zoom = zoom
      moved = true
      this.applyZoom()
    }
    if (moved) this.sync()
    this.flowSparks(dt)
    this.pulse(t)
    this.runEffects()
    this.runFlashes()
    if (this.frames++ % LABEL_EVERY === 0) this.placeAllLabels()
    this.composer.render()
    this.labels.render(this.scene, this.camera)
  }

  /** 把力导向算出来的位置放到场景里：先算每个模块的中心，拉远时文件往模块中心收拢 */
  private sync(): void {
    const collapse = COLLAPSE * this.zoom
    this.center.set(0, 0, 0)
    for (const s of this.stars) this.center.add(new THREE.Vector3(s.x ?? 0, s.y ?? 0, s.z ?? 0))
    if (this.stars.length) this.center.divideScalar(this.stars.length)
    for (const c of this.clusters.values()) {
      c.center.set(0, 0, 0)
      for (const s of c.stars) c.center.add(new THREE.Vector3(s.x ?? 0, s.y ?? 0, s.z ?? 0))
      if (c.stars.length) c.center.divideScalar(c.stars.length)
      let far = 0
      for (const s of c.stars) far = Math.max(far, Math.hypot((s.x ?? 0) - c.center.x, (s.y ?? 0) - c.center.y, (s.z ?? 0) - c.center.z))
      c.extent = far + 6
      c.sun.position.copy(c.center)
      c.halo.position.copy(c.center)
      c.nebula.position.copy(c.center)
      c.nebula.scale.setScalar(c.extent * 2.6 + c.radius * 3)
      c.label.position.set(c.center.x, c.center.y + c.radius + 3, c.center.z)
    }
    for (const g of this.galaxies) {
      let top = -Infinity
      const mid = new THREE.Vector3()
      for (const s of g.stars) {
        top = Math.max(top, s.y ?? 0)
        mid.add(new THREE.Vector3(s.x ?? 0, s.y ?? 0, s.z ?? 0))
      }
      if (g.stars.length) mid.divideScalar(g.stars.length)
      g.label.position.set(mid.x, top + 14, mid.z)
    }
    for (const p of this.planets.values()) {
      const s = p.star
      const c = this.clusters.get(s.mod)
      const pos = new THREE.Vector3(s.x ?? 0, s.y ?? 0, s.z ?? 0)
      if (c && !p.pinned) pos.lerp(c.center, collapse)
      s.at.copy(pos)
      p.mesh.position.copy(pos)
      p.halo.position.copy(pos)
      p.label?.position.set(pos.x, pos.y + s.radius + 2, pos.z)
    }
    if (this.lines) {
      const pos = this.lines.geometry.getAttribute('position') as THREE.BufferAttribute
      this.drawn.forEach((r, i) => {
        const a = (r.source as Star).at
        const b = (r.target as Star).at
        pos.setXYZ(i * 2, a.x, a.y, a.z)
        pos.setXYZ(i * 2 + 1, b.x, b.y, b.z)
      })
      pos.needsUpdate = true
      this.lines.geometry.computeBoundingSphere()
    }
    if (this.arcs) {
      const pos = this.arcs.geometry.getAttribute('position') as THREE.BufferAttribute
      const p = new THREE.Vector3()
      const q = new THREE.Vector3()
      this.bridges.forEach((r, i) => {
        const [a, ctrl, b] = this.arcOf(r)
        for (let s = 0; s < BRIDGE_SEGMENTS; s++) {
          bezier(a, ctrl, b, s / BRIDGE_SEGMENTS, p)
          bezier(a, ctrl, b, (s + 1) / BRIDGE_SEGMENTS, q)
          const k = (i * BRIDGE_SEGMENTS + s) * 2
          pos.setXYZ(k, p.x, p.y, p.z)
          pos.setXYZ(k + 1, q.x, q.y, q.z)
        }
      })
      pos.needsUpdate = true
      this.arcs.geometry.computeBoundingSphere()
    }
    this.placeLibs()
    if (this.moduleLines) {
      const pos = this.moduleLines.geometry.getAttribute('position') as THREE.BufferAttribute
      this.moduleRays.forEach((r, i) => {
        const a = this.clusters.get(r.source)?.center ?? this.center
        const b = this.clusters.get(r.target)?.center ?? this.center
        pos.setXYZ(i * 2, a.x, a.y, a.z)
        pos.setXYZ(i * 2 + 1, b.x, b.y, b.z)
      })
      pos.needsUpdate = true
      this.moduleLines.geometry.computeBoundingSphere()
    }
  }

  /**
   * 第三方库排在外圈：朝着用它的那些模块的方向（同一个方向的错开一点），连线到那些模块。
   * 圈的大小跟着整个星图走，排在正对镜头的平面上（只带一点纵深），和星系一样
   */
  private placeLibs(): void {
    if (!this.libs.length) return
    let far = 0
    for (const s of this.stars) far = Math.max(far, Math.hypot((s.x ?? 0) - this.center.x, (s.y ?? 0) - this.center.y))
    const ring = far + 70
    const wanted = this.libs.map((l) => {
      const dir = new THREE.Vector3()
      for (const m of l.modules) {
        const c = this.clusters.get(m)
        if (c) dir.add(c.center.clone().sub(this.center))
      }
      return Math.atan2(dir.y, dir.x)
    })
    // 同一个方向的错开：按角度排，挨得太近的往后推
    const order = wanted.map((a, i) => ({ a, i })).sort((x, y) => x.a - y.a)
    const gap = Math.min(0.35, (Math.PI * 2) / (this.libs.length * 1.4))
    for (let k = 1; k < order.length; k++) {
      if (order[k].a - order[k - 1].a < gap) order[k].a = order[k - 1].a + gap
    }
    for (const { a, i } of order) {
      const l = this.libs[i]
      l.at.set(this.center.x + Math.cos(a) * ring, this.center.y + Math.sin(a) * ring * 0.8, this.center.z + Math.sin(a) * ring * 0.1)
      l.mesh.position.copy(l.at)
      l.halo.position.copy(l.at)
      l.label.position.set(l.at.x, l.at.y + l.radius + 3, l.at.z)
    }
    if (this.libLines) {
      const pos = this.libLines.geometry.getAttribute('position') as THREE.BufferAttribute
      let k = 0
      for (const l of this.libs) {
        for (const m of l.modules) {
          const c = this.clusters.get(m)?.center ?? this.center
          pos.setXYZ(k++, l.at.x, l.at.y, l.at.z)
          pos.setXYZ(k++, c.x, c.y, c.z)
        }
      }
      pos.needsUpdate = true
      this.libLines.geometry.computeBoundingSphere()
    }
  }

  /** 一条跨进程弧线：两头是文件（画出来的位置），中间往上、往外拱起来 */
  private arcOf(r: Ray): [THREE.Vector3, THREE.Vector3, THREE.Vector3] {
    const a = (r.source as Star).at
    const b = (r.target as Star).at
    const mid = a.clone().add(b).multiplyScalar(0.5)
    const len = a.distanceTo(b)
    const out = mid.clone().sub(this.center)
    if (out.lengthSq() < 1e-6) out.set(0, 1, 0)
    const ctrl = mid.add(out.normalize().multiplyScalar(len * 0.18)).add(new THREE.Vector3(0, len * 0.32, 0))
    return [a, ctrl, b]
  }

  private flowSparks(dt: number): void {
    const h = this.highlight
    if (this.sparks) {
      const pos = this.sparks.geometry.getAttribute('position') as THREE.BufferAttribute
      const owner = this.sparks.geometry.userData.owner as Uint32Array
      owner.forEach((i, k) => {
        const r = this.rays[i]
        const speed = h?.links.has(r.key) ? 0.9 : 0.25
        this.sparkPhase[k] = (this.sparkPhase[k] + dt * speed) % 1
        const f = this.sparkPhase[k]
        const a = (r.source as Star).at
        const b = (r.target as Star).at
        pos.setXYZ(k, a.x + (b.x - a.x) * f, a.y + (b.y - a.y) * f, a.z + (b.z - a.z) * f)
      })
      pos.needsUpdate = true
      this.sparks.geometry.computeBoundingSphere()
    }
    if (this.arcSparks) {
      const pos = this.arcSparks.geometry.getAttribute('position') as THREE.BufferAttribute
      const owner = this.arcSparks.geometry.userData.owner as Uint32Array
      const p = new THREE.Vector3()
      owner.forEach((i, k) => {
        const r = this.bridges[i]
        this.arcPhase[k] = (this.arcPhase[k] + dt * 0.22) % 1
        const [a, ctrl, b] = this.arcOf(r)
        bezier(a, ctrl, b, this.arcPhase[k], p)
        pos.setXYZ(k, p.x, p.y, p.z)
      })
      pos.needsUpdate = true
      this.arcSparks.geometry.computeBoundingSphere()
    }
  }

  /** 出错的星球一跳一跳的，选中的慢慢呼吸；拉远时文件变小 */
  private pulse(t: number): void {
    const shrink = 1 - 0.45 * this.zoom
    for (const p of this.planets.values()) {
      const r = p.star.radius * (p.lit ? 1 : shrink)
      let k = 1
      if (this.highlight?.error.has(p.star.id)) k = 1 + 0.18 * Math.sin(t * 6)
      else if (p.star.id === this.selected) k = 1 + 0.06 * Math.sin(t * 2.5)
      p.mesh.scale.setScalar(r * k)
      const hs = (p.halo.userData.scale as number | undefined) ?? haloScale(this.zoom)
      p.halo.scale.setScalar(r * (p.lit ? 4.2 : hs) * (this.highlight?.error.has(p.star.id) ? 1.4 * k : k))
    }
  }

  /**
   * 名字防重叠：星系名最先摆；拉远时模块名优先，拉近时文件名优先（报错、选中、改过的文件始终最先）。
   * 屏幕外的、被挡住的先不显示
   */
  private placeAllLabels(): void {
    const w = this.el.clientWidth
    const hgt = this.el.clientHeight
    const far = this.zoom >= 0.45
    const h = this.highlight
    const boxes: LabelBox[] = []
    const objects = new Map<string, CSS2DObject>()
    const v = new THREE.Vector3()
    const add = (id: string, obj: CSS2DObject, text: string, size: number, lift: number, priority: number): void => {
      v.copy(obj.position).project(this.camera)
      if (v.z > 1 || Math.abs(v.x) > 1.02 || Math.abs(v.y) > 1.02) return
      const bw = textWidth(text, size) + 14
      const bh = size + 6
      boxes.push({ id, x: ((v.x + 1) / 2) * w, y: ((1 - v.y) / 2) * hgt - lift - bh / 2, w: bw, h: bh, priority })
      objects.set(id, obj)
    }
    for (const g of this.galaxies) add(`g:${g.group}`, g.label, g.group, 18, 0, 10_000)
    // 第三方库是配角：悬停、选中、选中的文件用到它时才写名字
    if (this.showLibs) for (const l of this.libs) if (this.libHot(l)) add(`l:${l.lib.id}`, l.label, l.lib.name, 13, 0, 7_000 + l.lib.count)
    for (const c of this.clusters.values()) {
      if (h && !c.stars.some((s) => h.error.has(s.id) || h.chain.has(s.id))) continue
      const rm = this.relatedMods
      if (rm && c.info.id !== rm.focus && !rm.out.has(c.info.id) && !rm.in.has(c.info.id)) continue // 指着模块时只写相连模块的名字
      const size = Math.log10(c.info.lines + 10)
      add(`m:${c.info.id}`, c.label, c.info.label, 13, 0, (far ? 5_000 : 50) + size * (c.info.test ? 1 : 10))
    }
    for (const p of this.planets.values()) {
      if (!p.label) continue
      const wanted = p.lit || (!h && !far && this.showLabels.has(p.star.id))
      if (!wanted) continue
      const rank = (p.star.test ? 20 : 100) + Math.log10(p.star.lines + 10) * 10 // 测试文件的名字排在后面
      add(`f:${p.star.id}`, p.label, p.star.name, 12, 10, p.lit ? 9_000 : rank)
    }
    const shown = placeLabels(boxes, 3, this.reserved)
    for (const g of this.galaxies) g.label.visible = false
    for (const l of this.libs) l.label.visible = false
    for (const c of this.clusters.values()) c.label.visible = false
    for (const p of this.planets.values()) if (p.label) p.label.visible = false
    for (const [id, obj] of objects) obj.visible = shown.has(id)
  }

  /** 这个库现在该不该亮：悬停、选中，或者选中的文件用到它 */
  private libHot(l: LibStar): boolean {
    const focusId = this.hovered ?? this.selected
    return l.lib.id === this.hoveredLib || l.lib.id === this.selectedLib || (!!focusId && l.lib.users.includes(focusId))
  }

  /** 界面上盖在星图上面的东西（工具栏、图例、面板）在哪：名字避开它们（相对画布的像素） */
  setReserved(rects: { x: number; y: number; w: number; h: number }[]): void {
    this.reserved = rects.map((r, i) => ({ id: `ui:${i}`, x: r.x + r.w / 2, y: r.y + r.h / 2, w: r.w, h: r.h, priority: Infinity }))
  }

  private flyTo(id: string): void {
    const p = this.planets.get(id)
    if (!p) return
    const s = p.star
    this.flyToPoint(new THREE.Vector3(s.x ?? 0, s.y ?? 0, s.z ?? 0), s.radius * 9 + 75) // 留点余地，看得到旁边的文件
  }

  /** 飞到一个模块跟前：大星散开成文件 */
  private flyToModule(id: string): void {
    const c = this.clusters.get(id)
    if (c) this.flyToPoint(c.center.clone(), Math.max(45, c.extent * 2.4 + 30))
  }

  private flyToPoint(aim: THREE.Vector3, distance: number): void {
    const dir = this.camera.position.clone().sub(this.controls.target).normalize()
    const to = aim.clone().add(dir.multiplyScalar(distance))
    this.fly = { from: this.camera.position.clone(), to, aimFrom: this.controls.target.clone(), aimTo: aim, start: performance.now() }
    this.sway = null
  }

  /**
   * 没人动镜头时左右轻轻晃（不转整圈）：星系排在正对镜头的平面上，转到侧面会前后挡住，晃一晃既有立体感又看得清
   */
  private swayStep(elapsed: number): void {
    if (!this.sway || this.fly) return
    const offset = this.camera.position.clone().sub(this.controls.target)
    const s = new THREE.Spherical().setFromVector3(offset)
    s.theta = this.sway.theta + SWAY * Math.sin((elapsed - this.sway.start) * 0.13)
    this.camera.position.copy(this.controls.target).add(new THREE.Vector3().setFromSpherical(s))
  }

  private flyStep(): void {
    if (!this.fly) return
    const f = Math.min(1, (performance.now() - this.fly.start) / FLY_MS)
    const e = f < 0.5 ? 4 * f * f * f : 1 - Math.pow(-2 * f + 2, 3) / 2 // 先慢后快再慢
    this.camera.position.lerpVectors(this.fly.from, this.fly.to, e)
    this.controls.target.lerpVectors(this.fly.aimFrom, this.fly.aimTo, e)
    if (f >= 1) this.fly = null
  }

  // ---------- 鼠标 ----------

  /** 鼠标下面是什么：拉远时先看模块的大星，拉近时先看文件 */
  private pick(clientX: number, clientY: number): Planet | Cluster | LibStar | null {
    const rect = this.renderer.domElement.getBoundingClientRect()
    this.pointer.set(((clientX - rect.left) / rect.width) * 2 - 1, -((clientY - rect.top) / rect.height) * 2 + 1)
    this.scene.updateMatrixWorld() // 还没画过一帧（窗口在后台）时，位置也要是新的
    this.raycaster.setFromCamera(this.pointer, this.camera)
    const planet = (): Planet | null => {
      const meshes = [...this.planets.values()].filter((p) => p.lit || this.zoom < 0.6).map((p) => p.mesh)
      const hit = this.raycaster.intersectObjects(meshes, false)[0]
      return hit ? (this.planets.get(hit.object.userData.id as string) ?? null) : null
    }
    const cluster = (): Cluster | null => {
      if (this.zoom < 0.4) return null
      const hit = this.raycaster.intersectObjects([...this.clusters.values()].map((c) => c.sun), false)[0]
      return hit ? (this.clusters.get(hit.object.userData.module as string) ?? null) : null
    }
    if (this.showLibs) {
      const hit = this.raycaster.intersectObjects(this.libs.map((l) => l.mesh), false)[0]
      const lib = hit && this.libs.find((l) => l.lib.id === hit.object.userData.lib)
      if (lib) return lib
    }
    return this.zoom >= 0.5 ? (cluster() ?? planet()) : (planet() ?? cluster())
  }

  private onPointerDown = (e: PointerEvent): void => {
    this.pressed = { x: e.clientX, y: e.clientY }
  }

  private onPointerUp = (e: PointerEvent): void => {
    const p = this.pressed
    this.pressed = null
    if (!p || Math.hypot(e.clientX - p.x, e.clientY - p.y) > 5) return // 是拖动，不是点击
    const hit = this.pick(e.clientX, e.clientY)
    if (hit && 'lib' in hit) {
      this.selected = null
      this.selectedLib = hit.lib.id // 点库：亮出它连到的模块，面板里列出谁在用
      this.applyStyles()
      this.onSelectLib(hit.lib)
      return
    }
    this.selectedLib = null
    this.onSelectLib(null)
    if (hit && 'info' in hit) {
      this.flyToModule(hit.info.id) // 点模块：飞过去，散开成文件
      this.setHovered(null, 0, 0)
      return
    }
    this.select(hit?.star.id ?? null)
    this.onSelect(hit?.star ?? null)
  }

  private onPointerMove = (e: PointerEvent): void => {
    if (this.pressed) return
    const hit = this.pick(e.clientX, e.clientY)
    const rect = this.renderer.domElement.getBoundingClientRect()
    this.setHovered(hit, e.clientX - rect.left, e.clientY - rect.top)
  }

  private setHovered(hit: Planet | Cluster | LibStar | null, x: number, y: number): void {
    const planet = hit && 'star' in hit ? hit : null
    const cluster = hit && 'info' in hit ? hit : null
    const lib = hit && 'lib' in hit ? hit : null
    const id = planet?.star.id ?? null
    const mod = cluster?.info.id ?? null
    const libId = lib?.lib.id ?? null
    this.renderer.domElement.style.cursor = hit ? 'pointer' : 'grab'
    if (id !== this.hovered || mod !== this.hoveredModule || libId !== this.hoveredLib) {
      this.hovered = id
      this.hoveredModule = mod
      this.hoveredLib = libId
      this.applyStyles()
    }
    this.onHover(planet?.star ?? null, x, y)
    this.onHoverModule(cluster?.info ?? null, x, y)
    this.onHoverLib(lib?.lib ?? null, x, y)
  }

  private fit(): void {
    const w = Math.max(1, this.el.clientWidth)
    const h = Math.max(1, this.el.clientHeight)
    this.renderer.setSize(w, h)
    this.labels.setSize(w, h)
    this.composer.setSize(w, h)
    this.bloom.setSize(w, h)
    this.camera.aspect = w / h
    this.camera.updateProjectionMatrix()
  }
}

function starId(s: Star | string): string {
  return typeof s === 'string' ? s : s.id
}

/** 二次贝塞尔曲线上的一点 */
function bezier(a: THREE.Vector3, c: THREE.Vector3, b: THREE.Vector3, t: number, out: THREE.Vector3): THREE.Vector3 {
  const u = 1 - t
  return out.set(
    u * u * a.x + 2 * u * t * c.x + t * t * b.x,
    u * u * a.y + 2 * u * t * c.y + t * t * b.y,
    u * u * a.z + 2 * u * t * c.z + t * t * b.z
  )
}

/** 一批线段的几何（位置、颜色每帧改） */
function lineGeometry(vertices: number): THREE.BufferGeometry {
  const geo = new THREE.BufferGeometry()
  geo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(vertices * 3), 3))
  geo.setAttribute('color', new THREE.BufferAttribute(new Float32Array(vertices * 3), 3))
  return geo
}

/** 一批光点：counts[i] 是第 i 条光线上有几个；userData.owner 记着每个光点属于哪条光线 */
function sparkGeometry(counts: number[]): { geometry: THREE.BufferGeometry; phase: Float32Array } {
  const total = counts.reduce((a, b) => a + b, 0)
  const geometry = lineGeometry(total)
  const phase = new Float32Array(total)
  const owner = new Uint32Array(total)
  let k = 0
  counts.forEach((c, i) => {
    for (let j = 0; j < c; j++) {
      owner[k] = i
      phase[k] = Math.random()
      k++
    }
  })
  geometry.userData.owner = owner
  return { geometry, phase }
}

/** 中间亮、往外淡的圆形贴图：光晕和光点都用它 */
function makeGlowTexture(): THREE.Texture {
  const size = 128
  const canvas = document.createElement('canvas')
  canvas.width = canvas.height = size
  const ctx = canvas.getContext('2d')!
  const g = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2)
  g.addColorStop(0, 'rgba(255,255,255,1)')
  g.addColorStop(0.2, 'rgba(255,255,255,0.65)')
  g.addColorStop(0.5, 'rgba(255,255,255,0.15)')
  g.addColorStop(1, 'rgba(255,255,255,0)')
  ctx.fillStyle = g
  ctx.fillRect(0, 0, size, size)
  const tex = new THREE.CanvasTexture(canvas)
  tex.colorSpace = THREE.SRGBColorSpace
  return tex
}

/** 远处暗暗的星空，让人知道自己在转 */
function makeStarfield(): THREE.Points {
  const count = 2500
  const pos = new Float32Array(count * 3)
  for (let i = 0; i < count; i++) {
    const r = 700 + Math.random() * 900
    const theta = Math.random() * Math.PI * 2
    const phi = Math.acos(2 * Math.random() - 1)
    pos.set([r * Math.sin(phi) * Math.cos(theta), r * Math.cos(phi), r * Math.sin(phi) * Math.sin(theta)], i * 3)
  }
  const geo = new THREE.BufferGeometry()
  geo.setAttribute('position', new THREE.BufferAttribute(pos, 3))
  return new THREE.Points(
    geo,
    new THREE.PointsMaterial({ color: 0x6f7da8, size: 1.4, sizeAttenuation: false, transparent: true, opacity: 0.55 })
  )
}

/** 星球的名字：HTML 元素，跟着星球在屏幕上的位置走 */
function makeLabel(text: string): CSS2DObject {
  const div = document.createElement('div')
  div.className = 'star-label'
  div.textContent = text
  Object.assign(div.style, {
    padding: '1px 6px',
    borderRadius: '4px',
    background: 'rgba(4, 8, 20, 0.6)',
    color: '#dbe9ff',
    font: '12px "Microsoft YaHei", sans-serif',
    whiteSpace: 'nowrap',
    transform: 'translateY(-10px)'
  })
  const label = new CSS2DObject(div)
  label.center.set(0.5, 1)
  return label
}

/** 模块的名字：写在星云上，用子项目的颜色、发一点光 */
function makeModuleLabel(text: string, color: string): CSS2DObject {
  const div = document.createElement('div')
  div.className = 'module-label'
  div.textContent = text
  Object.assign(div.style, {
    color,
    font: '600 13px "Microsoft YaHei", sans-serif',
    letterSpacing: '0.5px',
    whiteSpace: 'nowrap',
    textShadow: `0 0 8px ${color}, 0 0 2px #000`
  })
  const label = new CSS2DObject(div)
  label.center.set(0.5, 1)
  return label
}

/** 子项目（星系）的名字：大一号，写在星系上方 */
function makeGalaxyLabel(text: string, color: string): CSS2DObject {
  const div = document.createElement('div')
  div.className = 'galaxy-label'
  div.textContent = text
  Object.assign(div.style, {
    color: '#f2f7ff',
    font: '700 18px "Microsoft YaHei", sans-serif',
    letterSpacing: '3px',
    whiteSpace: 'nowrap',
    padding: '2px 12px',
    borderBottom: `2px solid ${color}`,
    textShadow: `0 0 14px ${color}`
  })
  const label = new CSS2DObject(div)
  label.center.set(0.5, 1)
  return label
}

const MAGIC_COLOR = '#7dffef'
const SEAL_COLOR = '#ffd27a'

function effectMaterial(color: string): THREE.MeshBasicMaterial {
  // 盖在星球上面画（不做深度测试），叠加发光
  return new THREE.MeshBasicMaterial({
    color,
    transparent: true,
    opacity: 0,
    blending: THREE.AdditiveBlending,
    depthTest: false,
    depthWrite: false,
    side: THREE.DoubleSide
  })
}

function ring(r: number, width: number, material: THREE.Material): THREE.Mesh {
  return new THREE.Mesh(new THREE.RingGeometry(r - width / 2, r + width / 2, 96), material)
}

/** 一批线段：points 两两一组 */
function segments(points: number[][], color: string): THREE.LineSegments {
  const geo = new THREE.BufferGeometry()
  geo.setAttribute('position', new THREE.Float32BufferAttribute(points.flatMap(([x, y]) => [x, y, 0]), 3))
  const mat = new THREE.LineBasicMaterial({
    color,
    transparent: true,
    opacity: 0,
    blending: THREE.AdditiveBlending,
    depthTest: false,
    depthWrite: false
  })
  return new THREE.LineSegments(geo, mat)
}

const at = (r: number, a: number): number[] => [r * Math.cos(a), r * Math.sin(a)]

/**
 * 法阵（自己画的几何图案）：外圈带刻度、两道细环、中间一个八角星和小圆。
 * 每一层是 group 的一个子节点，唤出时各层往不同方向转。
 */
function makeSigil(color: string): THREE.Group {
  const group = new THREE.Group()
  const m = effectMaterial(color)
  group.renderOrder = 10

  const outer = new THREE.Group()
  outer.add(ring(1, 0.025, m))
  const ticks: number[][] = []
  for (let i = 0; i < 72; i++) {
    const a = (i / 72) * Math.PI * 2
    const long = i % 6 === 0
    ticks.push(at(long ? 0.86 : 0.9, a), at(0.97, a))
  }
  outer.add(segments(ticks, color))

  const middle = new THREE.Group()
  middle.add(ring(0.82, 0.012, m), ring(0.76, 0.02, m))
  const dots: number[][] = []
  for (let i = 0; i < 12; i++) {
    const a = (i / 12) * Math.PI * 2 + Math.PI / 12
    dots.push(at(0.76, a), at(0.82, a))
  }
  middle.add(segments(dots, color))

  const star = new THREE.Group()
  const lines: number[][] = []
  for (let i = 0; i < 8; i++) {
    const a = (i / 8) * Math.PI * 2
    lines.push(at(0.72, a), at(0.72, a + (3 / 8) * Math.PI * 2)) // 八角星：每个顶点连到隔两个的顶点
  }
  star.add(segments(lines, color))

  const core = new THREE.Group()
  core.add(ring(0.3, 0.018, m), ring(0.12, 0.03, m))
  const spokes: number[][] = []
  for (let i = 0; i < 6; i++) {
    const a = (i / 6) * Math.PI * 2
    spokes.push(at(0.12, a), at(0.3, a))
  }
  core.add(segments(spokes, color))

  group.add(outer, middle, star, core)
  return group
}

function makeRings(color: string): THREE.Group {
  const m = effectMaterial(color)
  const group = new THREE.Group()
  group.add(ring(1, 0.03, m), ring(0.9, 0.012, m), ring(0.55, 0.01, m))
  return group
}

function disposeTree(obj: THREE.Object3D): void {
  const done = new Set<unknown>()
  obj.traverse((o) => {
    const mesh = o as THREE.Mesh
    if (mesh.geometry && !done.has(mesh.geometry)) {
      done.add(mesh.geometry)
      mesh.geometry.dispose()
    }
    const mat = mesh.material as THREE.Material | undefined
    if (mat && !done.has(mat)) {
      done.add(mat)
      mat.dispose()
    }
  })
}
