/**
 * 代码星图：文件是星球，文件之间的调用是光线（光点沿调用方向流动）。
 *
 * - 布局：d3-force-3d 力导向。光线把相关的文件拉近，星球之间互相排斥；每个子项目被拉向自己的「星系」中心
 * - 画：Three.js。星球 = 球体 + 叠加发光的光晕；光线 = 一批线段（一次绘制）；光点 = 一批点（一次绘制）；
 *   最后过一遍泛光（UnrealBloom），出全息的感觉
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
import type { CodeGraph, GraphNode } from '../../../shared/workbench'
import {
  CHAIN_COLOR,
  READ_COLOR,
  TOUCH_COLOR,
  ERROR_COLOR,
  groupAnchors,
  groupColors,
  labelled,
  linkKey,
  nodeRadius,
  type Highlight
} from './layout'

interface Star extends GraphNode {
  x?: number
  y?: number
  z?: number
  radius: number
  color: THREE.Color
}

interface Ray {
  source: Star | string
  target: Star | string
  weight: number
  key: string
}

interface Planet {
  star: Star
  mesh: THREE.Mesh<THREE.SphereGeometry, THREE.MeshBasicMaterial>
  halo: THREE.Sprite
  label: CSS2DObject | null
}

const BACKGROUND = 0x04060d
const DIM = 0.22 // 有报错高亮时，其余的星球暗到多少
const FLY_MS = 900
const FLASH_MS = 1200

export class StarMap {
  /** 点了星球（点空白处是 null） */
  onSelect: (node: GraphNode | null) => void = () => {}
  /** 鼠标移到星球上（移开是 null）；坐标是相对画布的 */
  onHover: (node: GraphNode | null, x: number, y: number) => void = () => {}

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
  private stars: Star[] = []
  private rays: Ray[] = []
  private lines: THREE.LineSegments | null = null
  private sparks: THREE.Points | null = null
  private sparkPhase: Float32Array = new Float32Array(0)
  private sim: Simulation<Star> | null = null
  private highlight: Highlight | null = null
  private selected: string | null = null
  private hovered: string | null = null
  private showLabels = new Set<string>()
  private fly: { from: THREE.Vector3; to: THREE.Vector3; aimFrom: THREE.Vector3; aimTo: THREE.Vector3; start: number } | null = null
  private raycaster = new THREE.Raycaster()
  private pointer = new THREE.Vector2()
  private pressed: { x: number; y: number } | null = null
  private clock = new THREE.Clock()
  private frame = 0
  /** 用户自己转过、缩放过镜头：布局稳定后就不再自动取景 */
  private userMoved = false
  /** 正在放的特效（法阵、光环） */
  private effects: { obj: THREE.Object3D; start: number; ms: number; step: (obj: THREE.Object3D, f: number) => number }[] = []
  /** 工作窗口里她这次改过的文件（变绿、显示名字） */
  private touched = new Set<string>()
  /** 星球上闪一下的光环（她在读、刚改了这个文件） */
  private flashes: { sprite: THREE.Sprite; start: number; radius: number }[] = []
  private resize: ResizeObserver

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
    this.controls.autoRotate = true
    this.controls.autoRotateSpeed = 0.35
    this.controls.addEventListener('start', () => {
      this.controls.autoRotate = false // 用户动手了就不自己转了
      this.fly = null
      this.userMoved = true
    })

    this.composer = new EffectComposer(this.renderer)
    this.composer.addPass(new RenderPass(this.scene, this.camera))
    // 只让亮的东西（星球、光点）发光：门槛太低整个画面会发灰
    this.bloom = new UnrealBloomPass(new THREE.Vector2(1, 1), 0.7, 0.4, 0.35)
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
    const anchors = groupAnchors(groups)
    this.showLabels = labelled(graph.nodes)

    this.stars = graph.nodes.map((n) => {
      const [ax, ay, az] = anchors.get(n.group) ?? [0, 0, 0]
      const base = new THREE.Color(colors.get(n.group) ?? '#8fa8ff')
      // 没提交的改动亮一点（往白色混一些）
      const color = n.modified ? base.clone().lerp(new THREE.Color('#ffffff'), 0.28) : base
      return {
        ...n,
        radius: nodeRadius(n.lines),
        color,
        x: ax + (Math.random() - 0.5) * 40,
        y: ay + (Math.random() - 0.5) * 40,
        z: az + (Math.random() - 0.5) * 40
      }
    })
    const known = new Set(this.stars.map((s) => s.id))
    this.rays = graph.links
      .filter((l) => known.has(l.source) && known.has(l.target))
      .map((l) => ({ source: l.source, target: l.target, weight: l.weight, key: linkKey(l) }))

    for (const star of this.stars) this.addPlanet(star)
    this.buildRays()

    this.sim = forceSimulation<Star>(this.stars, 3)
      .force(
        'link',
        forceLink<Star, Ray>(this.rays)
          .id((s) => s.id)
          .distance((r) => 16 + 26 / Math.sqrt(r.weight))
          .strength(0.25)
      )
      .force('charge', forceManyBody<Star>().strength(-38).distanceMax(220))
      .force('collide', forceCollide<Star>((s) => s.radius + 1.5).strength(0.8))
      .force('x', forceX<Star>((s) => anchors.get(s.group)?.[0] ?? 0).strength(0.05))
      .force('y', forceY<Star>((s) => anchors.get(s.group)?.[1] ?? 0).strength(0.05))
      .force('z', forceZ<Star>((s) => anchors.get(s.group)?.[2] ?? 0).strength(0.05))
      .stop()
    this.sim.tick(300) // 先在后台排好大半，一打开就是差不多的样子（一两百个文件不到 0.1 秒）；剩下的几秒慢慢稳定
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
    return this.pick(rect.left + x * rect.width, rect.top + y * rect.height)?.star ?? null
  }

  /** 手势光标移到星球上：和鼠标悬停一样显示名字 */
  hoverAt(p: { x: number; y: number } | null): void {
    if (!p) return this.setHovered(null, 0, 0)
    const rect = this.renderer.domElement.getBoundingClientRect()
    const planet = this.pick(rect.left + p.x * rect.width, rect.top + p.y * rect.height)
    this.setHovered(planet, p.x * rect.width, p.y * rect.height)
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
    this.controls.autoRotate = false
    this.fly = null
    this.userMoved = true
  }

  /** 镜头拉远，看整个星图 */
  frameAll(): void {
    const box = new THREE.Box3()
    for (const p of this.planets.values()) box.expandByPoint(p.mesh.position)
    if (box.isEmpty()) return
    const center = box.getCenter(new THREE.Vector3())
    const size = box.getSize(new THREE.Vector3()).length()
    this.camera.position.copy(center).add(new THREE.Vector3(0, size * 0.25, size * 0.95 + 60))
    this.controls.target.copy(center)
    this.controls.update()
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
    for (const p of this.planets.values()) {
      this.scene.remove(p.mesh, p.halo)
      p.mesh.material.dispose()
      p.halo.material.dispose()
      if (p.label) {
        this.scene.remove(p.label)
        p.label.element.remove()
      }
    }
    this.planets.clear()
    for (const obj of [this.lines, this.sparks]) {
      if (!obj) continue
      this.scene.remove(obj)
      obj.geometry.dispose()
      ;(obj.material as THREE.Material).dispose()
    }
    this.lines = this.sparks = null
    this.selected = this.hovered = null
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
    const halo = new THREE.Sprite(
      new THREE.SpriteMaterial({
        map: this.glow,
        color: star.color,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
        transparent: true,
        opacity: 0.55
      })
    )
    halo.scale.setScalar(star.radius * 4.2)
    this.scene.add(mesh, halo)
    this.planets.set(star.id, { star, mesh, halo, label: null })
  }

  private buildRays(): void {
    const n = this.rays.length
    const lineGeo = new THREE.BufferGeometry()
    lineGeo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(n * 6), 3))
    lineGeo.setAttribute('color', new THREE.BufferAttribute(new Float32Array(n * 6), 3))
    this.lines = new THREE.LineSegments(
      lineGeo,
      new THREE.LineBasicMaterial({
        vertexColors: true,
        transparent: true,
        opacity: 0.5,
        blending: THREE.AdditiveBlending,
        depthWrite: false
      })
    )
    // 光点：每条光线一个，调用多的两个，沿调用方向流动
    const counts = this.rays.map((r) => (r.weight > 3 ? 2 : 1))
    const total = counts.reduce((a, b) => a + b, 0)
    const sparkGeo = new THREE.BufferGeometry()
    sparkGeo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(total * 3), 3))
    sparkGeo.setAttribute('color', new THREE.BufferAttribute(new Float32Array(total * 3), 3))
    this.sparkPhase = new Float32Array(total)
    const owner = new Uint32Array(total)
    let k = 0
    counts.forEach((c, i) => {
      for (let j = 0; j < c; j++) {
        owner[k] = i
        this.sparkPhase[k] = Math.random()
        k++
      }
    })
    sparkGeo.userData.owner = owner
    this.sparks = new THREE.Points(
      sparkGeo,
      new THREE.PointsMaterial({
        size: 2.2,
        map: this.glow,
        vertexColors: true,
        transparent: true,
        blending: THREE.AdditiveBlending,
        depthWrite: false
      })
    )
    this.scene.add(this.lines, this.sparks)
    this.applyStyles()
  }

  /** 按现在的选中、悬停、报错高亮，给星球、光线、光点上色 */
  private applyStyles(): void {
    const h = this.highlight
    const lit = (id: string): boolean =>
      !h || h.error.has(id) || h.chain.has(id) || id === this.selected || this.touched.has(id)
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
      const on = lit(id)
      p.mesh.material.opacity = on ? 1 : DIM
      p.halo.material.opacity = on ? (id === this.selected ? 0.95 : 0.55) : DIM * 0.6
      const labelOn = id === this.selected || id === this.hovered || !!h?.error.has(id) || !!h?.chain.has(id) ||
        this.touched.has(id) || (!h && this.showLabels.has(id))
      if (labelOn && !p.label) {
        p.label = makeLabel(p.star.name)
        p.label.position.copy(p.mesh.position).y += p.star.radius + 2
        this.scene.add(p.label)
      }
      if (p.label) {
        p.label.visible = labelOn
        p.label.element.classList.toggle('error', !!h?.error.has(id))
      }
    }

    if (this.lines) {
      const colors = this.lines.geometry.getAttribute('color') as THREE.BufferAttribute
      this.rays.forEach((r, i) => {
        const hot = h?.links.has(r.key)
        const src = this.planets.get(starId(r.source))
        const near = this.selected && (starId(r.source) === this.selected || starId(r.target) === this.selected)
        const c = hot ? new THREE.Color(ERROR_COLOR) : (src?.star.color ?? new THREE.Color('#8fa8ff')).clone()
        const k = hot ? 1 : h ? 0.08 : near ? 1 : 0.5
        c.multiplyScalar(k)
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
        const c = hot ? new THREE.Color(CHAIN_COLOR) : (this.planets.get(starId(r.source))?.star.color ?? new THREE.Color('#ffffff')).clone()
        c.multiplyScalar(hot ? 1.2 : h ? 0.1 : 0.85)
        colors.setXYZ(k, c.r, c.g, c.b)
      })
      colors.needsUpdate = true
    }
  }

  // ---------- 每一帧 ----------

  private loop = (): void => {
    this.frame = requestAnimationFrame(this.loop)
    const dt = Math.min(this.clock.getDelta(), 0.05)
    const t = this.clock.elapsedTime
    if (this.sim && this.sim.alpha() > this.sim.alphaMin()) {
      this.sim.tick()
      this.sync()
      // 刚稳定下来：星图还在往外散的时候取的景不准，再取一次（用户动过镜头、正在飞向某颗星就不管）
      if (this.sim.alpha() <= this.sim.alphaMin() && !this.userMoved && !this.fly && !this.selected) this.frameAll()
    }
    this.flowSparks(dt)
    this.pulse(t)
    this.flyStep()
    this.controls.update()
    this.runEffects()
    this.runFlashes()
    this.composer.render()
    this.labels.render(this.scene, this.camera)
  }

  /** 把力导向算出来的位置放到场景里 */
  private sync(): void {
    for (const p of this.planets.values()) {
      const { x = 0, y = 0, z = 0, radius } = p.star
      p.mesh.position.set(x, y, z)
      p.halo.position.set(x, y, z)
      p.label?.position.set(x, y + radius + 2, z)
    }
    if (this.lines) {
      const pos = this.lines.geometry.getAttribute('position') as THREE.BufferAttribute
      this.rays.forEach((r, i) => {
        const a = r.source as Star
        const b = r.target as Star
        pos.setXYZ(i * 2, a.x ?? 0, a.y ?? 0, a.z ?? 0)
        pos.setXYZ(i * 2 + 1, b.x ?? 0, b.y ?? 0, b.z ?? 0)
      })
      pos.needsUpdate = true
      this.lines.geometry.computeBoundingSphere()
    }
  }

  private flowSparks(dt: number): void {
    if (!this.sparks) return
    const pos = this.sparks.geometry.getAttribute('position') as THREE.BufferAttribute
    const owner = this.sparks.geometry.userData.owner as Uint32Array
    const h = this.highlight
    owner.forEach((i, k) => {
      const r = this.rays[i]
      const speed = h?.links.has(r.key) ? 0.9 : 0.25
      this.sparkPhase[k] = (this.sparkPhase[k] + dt * speed) % 1
      const f = this.sparkPhase[k]
      const a = r.source as Star
      const b = r.target as Star
      pos.setXYZ(k, lerp(a.x, b.x, f), lerp(a.y, b.y, f), lerp(a.z, b.z, f))
    })
    pos.needsUpdate = true
    this.sparks.geometry.computeBoundingSphere()
  }

  /** 出错的星球一跳一跳的，选中的慢慢呼吸 */
  private pulse(t: number): void {
    for (const p of this.planets.values()) {
      const r = p.star.radius
      let k = 1
      if (this.highlight?.error.has(p.star.id)) k = 1 + 0.18 * Math.sin(t * 6)
      else if (p.star.id === this.selected) k = 1 + 0.06 * Math.sin(t * 2.5)
      p.mesh.scale.setScalar(r * k)
      p.halo.scale.setScalar(r * 4.2 * (this.highlight?.error.has(p.star.id) ? 1.4 * k : k))
    }
  }

  private flyTo(id: string): void {
    const p = this.planets.get(id)
    if (!p) return
    const aim = p.mesh.position.clone()
    const dir = this.camera.position.clone().sub(this.controls.target).normalize()
    const to = aim.clone().add(dir.multiplyScalar(p.star.radius * 9 + 40))
    this.fly = { from: this.camera.position.clone(), to, aimFrom: this.controls.target.clone(), aimTo: aim, start: performance.now() }
    this.controls.autoRotate = false
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

  private pick(clientX: number, clientY: number): Planet | null {
    const rect = this.renderer.domElement.getBoundingClientRect()
    this.pointer.set(((clientX - rect.left) / rect.width) * 2 - 1, -((clientY - rect.top) / rect.height) * 2 + 1)
    this.raycaster.setFromCamera(this.pointer, this.camera)
    const hit = this.raycaster.intersectObjects([...this.planets.values()].map((p) => p.mesh), false)[0]
    return hit ? (this.planets.get(hit.object.userData.id as string) ?? null) : null
  }

  private onPointerDown = (e: PointerEvent): void => {
    this.pressed = { x: e.clientX, y: e.clientY }
  }

  private onPointerUp = (e: PointerEvent): void => {
    const p = this.pressed
    this.pressed = null
    if (!p || Math.hypot(e.clientX - p.x, e.clientY - p.y) > 5) return // 是拖动，不是点击
    const planet = this.pick(e.clientX, e.clientY)
    this.select(planet?.star.id ?? null)
    this.onSelect(planet?.star ?? null)
  }

  private onPointerMove = (e: PointerEvent): void => {
    if (this.pressed) return
    const planet = this.pick(e.clientX, e.clientY)
    const rect = this.renderer.domElement.getBoundingClientRect()
    this.setHovered(planet, e.clientX - rect.left, e.clientY - rect.top)
  }

  private setHovered(planet: Planet | null, x: number, y: number): void {
    const id = planet?.star.id ?? null
    this.renderer.domElement.style.cursor = id ? 'pointer' : 'grab'
    if (id !== this.hovered) {
      this.hovered = id
      this.applyStyles()
    }
    this.onHover(planet?.star ?? null, x, y)
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

function lerp(a = 0, b = 0, f: number): number {
  return a + (b - a) * f
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
