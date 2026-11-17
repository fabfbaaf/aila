// 艾拉的原创形象：用代码一块块搭出来，写成 VRM 1.0（node scripts/avatar/make-avatar.mjs）。
//
// 为什么用代码搭：形象必须是原创的，又要能放进开源仓库、随时改；不依赖 VRoid Studio 这类要手工操作的软件。
// 风格是干净的卡通（MToon 着色 + 描边），不追求手捏模型那样的细节。
//
// 设计：短短的银紫色波波头、前面两缕鬓发和刘海；大大的紫灰色眼睛（会跟着鼠标转）；
// 宽松的米白色连帽卫衣、长袖盖住手腕；藏青百褶裙、深色裤袜、白色运动鞋。
// 两个只属于她的细节：右边头发上一个紫色的「▍」光标发夹，卫衣左胸一个「>_」终端提示符。
//
// 骨骼是 VRM 1.0 的标准人形骨骼，T 字站姿、面朝 +Z、单位米；每个零件整块绑在一根骨骼上（刚性蒙皮），
// 关节处用球体互相盖住，弯手肘、弯膝盖时看不到缝。

import * as THREE from 'three'
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js'
import { mkdirSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { GlbWriter } from './glb.mjs'

const here = dirname(fileURLToPath(import.meta.url))
const OUT = join(here, '..', '..', 'src', 'renderer', 'public', 'avatar', 'aila.vrm')

// ---------- 颜色和材质 ----------

/** #rrggbb（sRGB）→ glTF 要的线性颜色 */
function lin(hex) {
  const c = new THREE.Color(hex) // three 默认按 sRGB 解析、存成线性
  return [c.r, c.g, c.b]
}

const MATERIALS = {
  skin: { color: '#f8e3d8', shade: '#f0cdc2', outline: true, shift: -0.45 },
  hair: { color: '#ddd5f3', shade: '#a99bd8', outline: true, doubleSided: true },
  hoodie: { color: '#f1eff7', shade: '#c9c3e0', outline: true },
  hoodieDark: { color: '#d9d4ea', shade: '#aaa2c9', outline: true },
  accent: { color: '#a78bfa', shade: '#7c5fe0', outline: false },
  skirt: { color: '#2e3354', shade: '#1d2140', outline: true, doubleSided: true },
  tights: { color: '#2b2c3a', shade: '#1a1b26', outline: true },
  shoe: { color: '#f6f5fb', shade: '#c9c6db', outline: true },
  eyeWhite: { color: '#ffffff', shade: '#e6e2f2', outline: false },
  iris: { color: '#6b5aa6', shade: '#4b3d82', outline: false },
  pupil: { color: '#2a2147', shade: '#2a2147', outline: false },
  highlight: { color: '#ffffff', shade: '#ffffff', outline: false },
  lash: { color: '#3b3352', shade: '#3b3352', outline: false, doubleSided: true },
  brow: { color: '#8f84b8', shade: '#8f84b8', outline: false },
  mouth: { color: '#b85d6c', shade: '#9c4a59', outline: false },
  blush: { color: '#f2a7ae', shade: '#f2a7ae', outline: false, alpha: 0.38 }
}

// ---------- 骨骼（世界坐标，T 字站姿） ----------

const BONES = {}
function bone(name, parent, pos) {
  BONES[name] = { name, parent, pos: new THREE.Vector3(...pos) }
}
bone('hips', null, [0, 0.8, 0])
bone('spine', 'hips', [0, 0.88, 0])
bone('chest', 'spine', [0, 1.0, 0])
bone('neck', 'chest', [0, 1.165, -0.005])
bone('head', 'neck', [0, 1.235, 0])
const HEAD_C = new THREE.Vector3(0, 1.335, 0) // 头这个球的中心
const HEAD_R = new THREE.Vector3(0.104, 0.112, 0.1)
for (const [side, s] of [['left', 1], ['right', -1]]) {
  bone(`${side}Shoulder`, 'chest', [s * 0.03, 1.12, 0])
  bone(`${side}UpperArm`, `${side}Shoulder`, [s * 0.1, 1.12, 0])
  bone(`${side}LowerArm`, `${side}UpperArm`, [s * 0.315, 1.12, 0])
  bone(`${side}Hand`, `${side}LowerArm`, [s * 0.505, 1.12, 0])
  bone(`${side}UpperLeg`, 'hips', [s * 0.072, 0.775, 0])
  bone(`${side}LowerLeg`, `${side}UpperLeg`, [s * 0.072, 0.43, 0.005])
  bone(`${side}Foot`, `${side}LowerLeg`, [s * 0.072, 0.075, 0])
}
// 眼球的转动中心在脸里面一点：骨骼一转，虹膜就在脸上滑动
const EYE = { y: 1.318, x: 0.043 }
function onFace(x, y, out = 0) {
  // 头这个椭球表面上 (x, y) 处的点和法线
  const dx = x / HEAD_R.x
  const dy = (y - HEAD_C.y) / HEAD_R.y
  const dz = Math.sqrt(Math.max(0, 1 - dx * dx - dy * dy))
  const p = new THREE.Vector3(x, y, HEAD_C.z + dz * HEAD_R.z)
  const n = new THREE.Vector3(dx / HEAD_R.x, dy / HEAD_R.y, dz / HEAD_R.z).normalize()
  return { p: p.addScaledVector(n, out), n }
}
for (const [side, s] of [['left', 1], ['right', -1]]) {
  const { p, n } = onFace(s * EYE.x, EYE.y)
  bone(`${side}Eye`, 'head', p.clone().addScaledVector(n, -0.045).toArray())
}

// ---------- 零件 ----------

/** meshName → { material, pieces: [{ geo, bone, morphs: { name: Float32Array } }] } */
const groups = new Map()

function add(meshName, material, geo, boneName, morphs = {}) {
  if (!groups.has(meshName)) groups.set(meshName, { material, pieces: [] })
  const g = geo.index ? geo : geo
  if (!g.getAttribute('uv')) g.setAttribute('uv', new THREE.Float32BufferAttribute(new Float32Array((g.getAttribute('position').count) * 2), 2))
  groups.get(meshName).pieces.push({ geo: g, bone: boneName, morphs })
}

function placed(geo, { pos = [0, 0, 0], rot = [0, 0, 0], scale = [1, 1, 1] } = {}) {
  const m = new THREE.Matrix4().compose(
    new THREE.Vector3(...pos),
    new THREE.Quaternion().setFromEuler(new THREE.Euler(...rot)),
    new THREE.Vector3(...scale)
  )
  return geo.applyMatrix4(m)
}

/** 两点之间的圆台（两头各一个球盖住接缝） */
function limb(a, b, r1, r2, seg = 18) {
  const A = new THREE.Vector3(...a)
  const B = new THREE.Vector3(...b)
  const len = A.distanceTo(B)
  const cyl = new THREE.CylinderGeometry(r2, r1, len, seg, 1, true)
  const q = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 1, 0), B.clone().sub(A).normalize())
  const mid = A.clone().add(B).multiplyScalar(0.5)
  cyl.applyMatrix4(new THREE.Matrix4().compose(mid, q, new THREE.Vector3(1, 1, 1)))
  const s1 = new THREE.SphereGeometry(r1, seg, 10).translate(...a)
  const s2 = new THREE.SphereGeometry(r2, seg, 10).translate(...b)
  return merge([cyl, s1, s2])
}

function merge(list) {
  return mergeGeometries(list.map((g) => (g.index ? g.toNonIndexed() : g)).map(fixAttrs), false)
}

function fixAttrs(g) {
  for (const k of Object.keys(g.attributes)) if (!['position', 'normal', 'uv'].includes(k)) g.deleteAttribute(k)
  if (!g.getAttribute('uv')) g.setAttribute('uv', new THREE.Float32BufferAttribute(new Float32Array(g.getAttribute('position').count * 2), 2))
  return g
}

/** 绕 Y 轴转出来的形状：points 是 [半径, 高度]，ripple(角度) 让半径随角度变（百褶） */
function lathe(points, seg, ripple = () => 1, phiStart = 0, phiLength = Math.PI * 2) {
  // 点要从下往上排，面才朝外（反过来的话正面被剔掉，看到的是里面，一片黑）
  const sorted = [...points].sort((a, b) => a[1] - b[1])
  const g = new THREE.LatheGeometry(sorted.map(([r, y]) => new THREE.Vector2(r, y)), seg, phiStart, phiLength)
  const pos = g.getAttribute('position')
  for (let i = 0; i < pos.count; i++) {
    const x = pos.getX(i)
    const z = pos.getZ(i)
    const k = ripple(Math.atan2(x, z))
    pos.setXYZ(i, x * k, pos.getY(i), z * k)
  }
  g.computeVertexNormals()
  return g
}

/**
 * 刘海：头外面一层（离脸 1 厘米多）的一片，从头顶往前盖到额头。
 * 下沿按角度分成九缕：每缕中间长、两边短，尖到眉毛那里，缕和缕之间露出一点额头。
 */
function fringe() {
  const R = HEAD_R.clone().add(new THREE.Vector3(0.012, 0.014, 0.014))
  const nu = 90
  const nv = 12
  const phiHalf = (78 * Math.PI) / 180
  const top = (22 * Math.PI) / 180
  const strands = 9
  const bottom = (u) => {
    const f = (((u + 1) / 2) * strands) % 1
    const tip = Math.pow(1 - Math.abs(2 * f - 1), 1.4) // 0 在缝里，1 在尖上
    const side = Math.abs(u) // 两边的缕更长一点，盖住太阳穴
    return ((67 + 15 * tip + 6 * side) * Math.PI) / 180
  }
  const pos = []
  const uv = []
  const idx = []
  for (let i = 0; i <= nu; i++) {
    const u = -1 + (2 * i) / nu
    const phi = u * phiHalf
    const tb = bottom(u)
    for (let j = 0; j <= nv; j++) {
      const th = top + ((tb - top) * j) / nv
      pos.push(R.x * Math.sin(th) * Math.sin(phi), HEAD_C.y + R.y * Math.cos(th), HEAD_C.z + R.z * Math.sin(th) * Math.cos(phi))
      uv.push(i / nu, j / nv)
    }
  }
  for (let i = 0; i < nu; i++) {
    for (let j = 0; j < nv; j++) {
      const a = i * (nv + 1) + j
      const b = a + nv + 1
      idx.push(a, b, a + 1, b, b + 1, a + 1)
    }
  }
  const g = new THREE.BufferGeometry()
  g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3))
  g.setAttribute('uv', new THREE.Float32BufferAttribute(uv, 2))
  g.setIndex(idx)
  g.computeVertexNormals()
  return g
}

// 头和脖子
{
  // 下半张脸收窄（瓜子脸），x 越往下缩得越多；脸的正中线（x=0）不变，眼睛、嘴的位置照样能用 onFace 算
  const head = placed(new THREE.SphereGeometry(1, 48, 32), { pos: HEAD_C.toArray(), scale: HEAD_R.toArray() })
  const pos = head.getAttribute('position')
  for (let i = 0; i < pos.count; i++) {
    const t = Math.max(0, (HEAD_C.y - pos.getY(i)) / HEAD_R.y)
    pos.setX(i, pos.getX(i) * (1 - 0.28 * t * t))
  }
  head.computeVertexNormals()
  add('Skin', 'skin', head, 'head')
}
add('Skin', 'skin', limb([0, 1.15, -0.005], [0, 1.255, 0], 0.027, 0.027), 'neck')

// 头发：头顶、后面的波波、鬓发、刘海、一撮呆毛
add('Hair', 'hair', placed(new THREE.SphereGeometry(1, 40, 20, 0, Math.PI * 2, 0, Math.PI * 0.42), {
  pos: [0, 1.345, -0.006], scale: [0.118, 0.13, 0.116]
}), 'head')
{
  const cy = HEAD_C.y
  const prof = []
  for (let i = 0; i <= 12; i++) {
    const y = 1.405 - (i / 12) * 0.16
    const base = HEAD_R.x * Math.sqrt(Math.max(0, 1 - ((y - cy) / 0.125) ** 2)) + 0.014
    const flare = i > 7 ? (i - 7) * 0.006 : 0
    const tuck = i === 12 ? -0.012 : 0
    prof.push([base + flare + tuck, y])
  }
  // 正面留出脸（前面 140° 不长头发），波波往外翘一点
  add('Hair', 'hair', placed(lathe(prof, 36, () => 1, Math.PI * 0.39, Math.PI * 2 - Math.PI * 0.78), { scale: [1.04, 1, 1.0] }), 'head')
}
for (const s of [1, -1]) {
  // 鬓发：耳朵前面两缕，到下巴
  const lock = new THREE.ConeGeometry(0.017, 0.14, 10, 1, false)
  lock.rotateX(Math.PI) // 尖朝下
  add('Hair', 'hair', placed(lock, { pos: [s * 0.088, 1.295, 0.045], rot: [0.12, s * 0.55, s * -0.06], scale: [1, 1, 0.32] }), 'head')
}
{
  // 刘海：七缕，沿着额头排开，尖到眉毛上面
  add('Hair', 'hair', fringe(), 'head')
  // 一小撮翘起来的呆毛，往后弯
  const ahoge = new THREE.TorusGeometry(0.026, 0.0045, 6, 18, Math.PI * 0.8)
  add('Hair', 'hair', placed(ahoge, { pos: [0.004, 1.462, -0.02], rot: [0, 0.35, 0.5] }), 'head')
}
// 发夹「▍」：右边（-X）
add('Accent', 'accent', placed(new THREE.BoxGeometry(0.008, 0.03, 0.006), { pos: [-0.094, 1.375, 0.055], rot: [0, -0.6, 0.15] }), 'head')

// 躯干：卫衣
const torso = lathe([[0.04, 1.175], [0.085, 1.158], [0.112, 1.12], [0.118, 1.06], [0.113, 1.0], [0.108, 0.96]], 32)
add('Hoodie', 'hoodie', placed(torso, { scale: [1, 1, 0.74] }), 'chest')
add('Hoodie', 'hoodie', placed(lathe([[0.108, 0.975], [0.11, 0.92], [0.114, 0.875]], 32), { scale: [1, 1, 0.76] }), 'spine')
add('HoodieDark', 'hoodieDark', placed(lathe([[0.118, 0.89], [0.119, 0.86], [0.112, 0.85]], 32), { scale: [1, 1, 0.78] }), 'spine') // 下摆
add('HoodieDark', 'hoodieDark', placed(new THREE.SphereGeometry(1, 24, 14), { pos: [0, 1.168, -0.045], scale: [0.085, 0.05, 0.06] }), 'chest') // 帽子
add('HoodieDark', 'hoodieDark', placed(lathe([[0.05, 1.17], [0.058, 1.155], [0.07, 1.145]], 28), { scale: [1, 1, 0.8] }), 'chest') // 领口
for (const s of [1, -1]) {
  // 帽绳
  add('Accent', 'accent', limb([s * 0.026, 1.15, 0.083], [s * 0.03, 1.06, 0.088], 0.0035, 0.0035, 8), 'chest')
  add('Accent', 'accent', placed(new THREE.SphereGeometry(0.007, 10, 8), { pos: [s * 0.03, 1.055, 0.089] }), 'chest')
}
{
  // 左胸（+X）的「>_」
  const z = 0.092
  add('Accent', 'accent', placed(new THREE.BoxGeometry(0.014, 0.004, 0.003), { pos: [0.052, 1.088, z], rot: [0, 0, -0.6] }), 'chest')
  add('Accent', 'accent', placed(new THREE.BoxGeometry(0.014, 0.004, 0.003), { pos: [0.052, 1.078, z], rot: [0, 0, 0.6] }), 'chest')
  add('Accent', 'accent', placed(new THREE.BoxGeometry(0.014, 0.004, 0.003), { pos: [0.072, 1.072, z - 0.002] }), 'chest')
}

// 裙子：百褶，下摆一圈浅紫
const pleat = (a) => 1 + 0.045 * Math.sign(Math.sin(a * 12))
add('Skirt', 'skirt', placed(lathe([[0.096, 0.866], [0.13, 0.8], [0.163, 0.71], [0.19, 0.625]], 48, pleat), { scale: [1, 1, 0.82] }), 'hips')
add('Accent', 'accent', placed(lathe([[0.193, 0.64], [0.199, 0.632], [0.2, 0.62]], 48, pleat), { scale: [1, 1, 0.82] }), 'hips')
add('Tights', 'tights', placed(new THREE.SphereGeometry(1, 20, 12), { pos: [0, 0.79, 0], scale: [0.11, 0.07, 0.08] }), 'hips')

// 腿：裤袜、鞋
for (const [side, s] of [['left', 1], ['right', -1]]) {
  add('Skin', 'skin', limb([s * 0.07, 0.74, 0], [s * 0.072, 0.43, 0.005], 0.058, 0.046), `${side}UpperLeg`)
  add('Tights', 'tights', limb([s * 0.072, 0.43, 0.005], [s * 0.072, 0.09, 0], 0.048, 0.034), `${side}LowerLeg`)
  add('Tights', 'tights', limb([s * 0.072, 0.44, 0.005], [s * 0.072, 0.4, 0.005], 0.05, 0.049), `${side}LowerLeg`)
  add('Accent', 'accent', placed(new THREE.TorusGeometry(0.0495, 0.004, 6, 24), { pos: [s * 0.072, 0.41, 0.005], rot: [Math.PI / 2, 0, 0] }), `${side}LowerLeg`)
  add('Shoe', 'shoe', placed(new THREE.SphereGeometry(1, 20, 12), { pos: [s * 0.072, 0.045, 0.028], scale: [0.042, 0.04, 0.085] }), `${side}Foot`)
  add('Accent', 'accent', placed(new THREE.TorusGeometry(1, 0.06, 6, 24), { pos: [s * 0.072, 0.03, 0.028], rot: [Math.PI / 2, 0, 0], scale: [0.043, 0.086, 0.043] }), `${side}Foot`)
}

// 手臂：长袖（袖口浅紫一圈），手
for (const [side, s] of [['left', 1], ['right', -1]]) {
  add('Hoodie', 'hoodie', limb([s * 0.095, 1.12, 0], [s * 0.315, 1.12, 0], 0.048, 0.04), `${side}UpperArm`)
  add('Hoodie', 'hoodie', limb([s * 0.315, 1.12, 0], [s * 0.49, 1.12, 0], 0.04, 0.046), `${side}LowerArm`)
  add('Accent', 'accent', placed(new THREE.TorusGeometry(0.046, 0.006, 8, 24), { pos: [s * 0.492, 1.12, 0], rot: [0, Math.PI / 2, 0] }), `${side}LowerArm`)
  add('Skin', 'skin', placed(new THREE.SphereGeometry(1, 20, 12), { pos: [s * 0.535, 1.12, 0], scale: [0.042, 0.017, 0.034] }), `${side}Hand`)
  add('Skin', 'skin', placed(new THREE.SphereGeometry(1, 12, 8), { pos: [s * 0.518, 1.115, 0.03], rot: [0, s * 0.5, 0], scale: [0.02, 0.011, 0.011] }), `${side}Hand`)
}

// ---------- 脸：眼睛、睫毛、眉毛、嘴、腮红（带表情的形变） ----------

/** 平面零件放到脸上：local 是贴在脸上的 2D 坐标（x 横、y 竖），out 是离脸多远 */
function facePart(geo, cx, cy, out) {
  const { p, n } = onFace(cx, cy, out)
  const q = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 0, 1), n)
  return new THREE.Matrix4().compose(p, q, new THREE.Vector3(1, 1, 1))
}

/**
 * 一个 2D 零件（在 xy 平面里），按 morphs 里的函数算出各个形变，再一起放到脸上。
 * morphs[name] = (x, y) => [x', y']（零件自己的坐标）
 */
function face2d(meshName, material, geo, boneName, cx, cy, out, morphs = {}) {
  const base = geo.getAttribute('position')
  const m = facePart(geo, cx, cy, out)
  const world = geo.clone().applyMatrix4(m)
  const deltas = {}
  for (const [name, fn] of Object.entries(morphs)) {
    const moved = geo.clone()
    const pos = moved.getAttribute('position')
    for (let i = 0; i < pos.count; i++) {
      const [x, y] = fn(base.getX(i), base.getY(i))
      pos.setXYZ(i, x, y, base.getZ(i))
    }
    moved.applyMatrix4(m)
    const a = moved.getAttribute('position').array
    const b = world.getAttribute('position').array
    deltas[name] = new Float32Array(a.length).map((_, k) => a[k] - b[k])
  }
  add(meshName, material, world, boneName, deltas)
}

const disc = (w, h, seg = 32) => new THREE.CircleGeometry(1, seg).scale(w, h, 1)
const W = 0.026 // 眼白半宽
const H = 0.029 // 眼白半高

for (const [side, s] of [['left', 1], ['right', -1]]) {
  const cx = s * EYE.x
  const cy = EYE.y
  // 闭眼：所有东西压到下眼睑那条线；笑眼（happy 用一半多）也是它
  const close = (lo) => (x, y) => [x, lo + (y - lo) * 0.06]
  const wide = (x, y) => [x * 1.08, y * 1.14]
  face2d('EyeWhite', 'eyeWhite', disc(W, H), 'head', cx, cy, 0.0012, { blink: close(-H * 0.55), wide })
  // 虹膜、瞳孔、高光绑在眼球骨骼上：骨骼一转，它们一起动
  const eyeBone = `${side}Eye`
  face2d('Iris', 'iris', disc(W * 0.86, H * 0.94), eyeBone, cx, cy + 0.0015, 0.0022, { blink: close(-H * 0.55 - 0.0015), wide })
  face2d('Pupil', 'pupil', disc(W * 0.38, H * 0.48), eyeBone, cx, cy + 0.0005, 0.0029, { blink: close(-H * 0.55 - 0.0005), wide })
  face2d('Highlight', 'highlight', merge([
    disc(0.0058, 0.0062, 16).translate(-0.006, 0.009, 0),
    disc(0.0025, 0.0025, 12).translate(0.007, -0.009, 0)
  ]), eyeBone, cx, cy, 0.0036, { blink: close(-H * 0.55 + 0.004), wide })

  // 上睫毛：沿眼睛上沿的一条弧，外眼角（|x| 大的那头）更粗、带个小尾巴
  {
    const pts = []
    const N = 20
    for (let i = 0; i <= N; i++) {
      const a = Math.PI * (0.06 + 0.88 * (i / N)) // 从右到左
      const outer = s > 0 ? Math.cos(a) > 0 : Math.cos(a) < 0 // 这只眼睛的外侧
      const t = 0.0026 + (outer ? Math.abs(Math.cos(a)) * 0.0028 : 0.0005)
      const x = Math.cos(a) * W * 1.1
      const y = Math.sin(a) * H * 1.02
      pts.push([x, y, t])
    }
    const pos = []
    const idx = []
    pts.forEach(([x, y, t]) => {
      const n = new THREE.Vector2(x / (W * W), y / (H * H)).normalize()
      pos.push(x - n.x * t * 0.3, y - n.y * t * 0.3, 0, x + n.x * t, y + n.y * t, 0)
    })
    for (let i = 0; i < pts.length - 1; i++) idx.push(2 * i, 2 * i + 2, 2 * i + 1, 2 * i + 1, 2 * i + 2, 2 * i + 3)
    const g = new THREE.BufferGeometry()
    g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3))
    g.setIndex(idx)
    g.computeVertexNormals()
    // 闭眼：整条往下到下眼睑，变成「⌒」；睁大：往上一点
    face2d('Lash', 'lash', g, 'head', cx, cy, 0.0042, {
      blink: (x, y) => [x * 1.02, y - H * 1.38 + 0.004],
      wide: (x, y) => [x * 1.08, y * 1.14]
    })
  }

  // 眉毛：细细的一条，往外侧低一点；难过时内侧抬起，惊讶时整条抬起
  {
    const g = new THREE.CircleGeometry(1, 20).scale(0.021, 0.0028, 1)
    const inner = (x) => (s > 0 ? -x : x) / 0.021 // 内侧为正（靠近鼻子那头）
    face2d('Brow', 'brow', g, 'head', s * 0.046, cy + 0.04, 0.005, {
      sad: (x, y) => [x, y + Math.max(0, inner(x)) * 0.007],
      angry: (x, y) => [x, y - Math.max(0, inner(x)) * 0.007],
      up: (x, y) => [x, y + 0.008]
    })
  }

  // 腮红
  face2d('Blush', 'blush', disc(0.017, 0.007, 20), 'head', s * 0.06, cy - 0.034, 0.0025)
}

// 嘴：闭着时是一条小小的、嘴角微微上翘的线；五个元音口型 + 笑 + 撇嘴
{
  const g = new THREE.CircleGeometry(1, 36)
  const shape = (w, h, dy = 0, curve = 0) => (x, y) => [x * w, y * h + dy + curve * x * x]
  const base = shape(0.0085, 0.0014, 0, 0.0016)
  // 基础形状先变成「闭嘴」，形变都相对于它
  const pos = g.getAttribute('position')
  const orig = Array.from(pos.array)
  for (let i = 0; i < pos.count; i++) {
    const [x, y] = base(orig[i * 3], orig[i * 3 + 1])
    pos.setXYZ(i, x, y, 0)
  }
  const from = (fn) => (x, y) => {
    // 由闭嘴的坐标反推单位圆上的点，再按新的口型放大
    const ux = x / 0.0085
    const uy = (y - 0.0016 * ux * ux) / 0.0014
    return fn(ux, uy)
  }
  face2d('Mouth', 'mouth', g, 'head', 0, 1.252, 0.0035, {
    aa: from(shape(0.0095, 0.0115, -0.004)),
    ih: from(shape(0.012, 0.0035, -0.001)),
    ou: from(shape(0.0055, 0.0065, -0.002)),
    ee: from(shape(0.012, 0.006, -0.002)),
    oh: from(shape(0.0075, 0.009, -0.003)),
    smile: from(shape(0.012, 0.0022, 0.001, 0.009)),
    frown: from(shape(0.008, 0.0016, 0, -0.01))
  })
}

// ---------- 写文件 ----------

const w = new GlbWriter()
const boneNames = Object.keys(BONES)
const boneIndex = {}
for (const name of boneNames) {
  const b = BONES[name]
  const parentPos = b.parent ? BONES[b.parent].pos : new THREE.Vector3()
  boneIndex[name] = w.node({ name: `J_${name}`, translation: b.pos.clone().sub(parentPos).toArray() })
}
for (const name of boneNames) {
  const children = boneNames.filter((n) => BONES[n].parent === name).map((n) => boneIndex[n])
  if (children.length) w.json.nodes[boneIndex[name]].children = children
}
w.json.scenes[0].nodes.push(boneIndex.hips)

// 蒙皮：关节 = 所有骨骼；逆绑定矩阵就是把骨骼的世界位置平移回原点
const ibm = new Float32Array(boneNames.length * 16)
boneNames.forEach((name, i) => {
  const m = new THREE.Matrix4().makeTranslation(...BONES[name].pos.clone().negate().toArray())
  ibm.set(m.elements, i * 16)
})
w.json.skins.push({ joints: boneNames.map((n) => boneIndex[n]), inverseBindMatrices: w.accessor(ibm, 'MAT4', 5126), skeleton: boneIndex.hips })
const jointOf = Object.fromEntries(boneNames.map((n, i) => [n, i]))

const materialIndex = {}
for (const [name, m] of Object.entries(MATERIALS)) {
  const base = [...lin(m.color), m.alpha ?? 1]
  materialIndex[name] = w.material({
    name,
    pbrMetallicRoughness: { baseColorFactor: base, metallicFactor: 0, roughnessFactor: 1 },
    alphaMode: m.alpha ? 'BLEND' : 'OPAQUE',
    doubleSided: !!m.doubleSided,
    extensions: {
      KHR_materials_unlit: {},
      VRMC_materials_mtoon: {
        specVersion: '1.0',
        transparentWithZWrite: false,
        renderQueueOffsetNumber: m.alpha ? 1 : 0,
        shadeColorFactor: lin(m.shade),
        shadingShiftFactor: m.shift ?? -0.15,
        shadingToonyFactor: 0.92,
        giEqualizationFactor: 0.9,
        matcapFactor: [0, 0, 0],
        parametricRimColorFactor: [0, 0, 0],
        rimLightingMixFactor: 1,
        parametricRimFresnelPowerFactor: 5,
        parametricRimLiftFactor: 0,
        outlineWidthMode: m.outline ? 'worldCoordinates' : 'none',
        outlineWidthFactor: m.outline ? 0.0011 : 0,
        outlineColorFactor: lin('#3b3352'),
        outlineLightingMixFactor: 1
      }
    }
  })
}

const meshNode = {}
const morphIndex = {} // meshName → { morphName: index }
for (const [meshName, g] of groups) {
  const morphNames = [...new Set(g.pieces.flatMap((p) => Object.keys(p.morphs)))]
  const position = []
  const normal = []
  const uv = []
  const joints = []
  const weights = []
  const index = []
  const deltas = Object.fromEntries(morphNames.map((n) => [n, []]))
  for (const piece of g.pieces) {
    const geo = piece.geo.index ? piece.geo : piece.geo
    if (!geo.getAttribute('normal')) geo.computeVertexNormals()
    const start = position.length / 3
    const p = geo.getAttribute('position')
    const nrm = geo.getAttribute('normal')
    const t = geo.getAttribute('uv')
    for (let i = 0; i < p.count; i++) {
      position.push(p.getX(i), p.getY(i), p.getZ(i))
      const nv = new THREE.Vector3(nrm.getX(i), nrm.getY(i), nrm.getZ(i)).normalize()
      normal.push(nv.x || 0, nv.y || 0, nv.z || 1)
      uv.push(t ? t.getX(i) : 0, t ? t.getY(i) : 0)
      joints.push(jointOf[piece.bone], 0, 0, 0)
      weights.push(1, 0, 0, 0)
    }
    if (geo.index) for (const k of geo.index.array) index.push(start + k)
    else for (let i = 0; i < p.count; i++) index.push(start + i)
    for (const name of morphNames) {
      const d = piece.morphs[name]
      for (let i = 0; i < p.count * 3; i++) deltas[name].push(d ? d[i] : 0)
    }
  }
  const morphs = morphNames.map((name) => ({ name, delta: new Float32Array(deltas[name]) }))
  meshNode[meshName] = w.mesh(meshName, {
    position: new Float32Array(position),
    normal: new Float32Array(normal),
    uv: new Float32Array(uv),
    joints: new Uint16Array(joints),
    weights: new Float32Array(weights),
    index
  }, materialIndex[g.material], 0, morphs)
  morphIndex[meshName] = Object.fromEntries(morphNames.map((n, i) => [n, i]))
}

// ---------- VRM 1.0 ----------

const bind = (mesh, morph, weight = 1) =>
  morphIndex[mesh]?.[morph] === undefined ? [] : [{ node: meshNode[mesh], index: morphIndex[mesh][morph], weight }]
const eyes = (morph, weight = 1) => ['EyeWhite', 'Iris', 'Pupil', 'Highlight', 'Lash'].flatMap((m) => bind(m, morph, weight))
const expr = (binds, extra = {}) => ({ morphTargetBinds: binds, isBinary: false, overrideBlink: 'none', overrideLookAt: 'none', overrideMouth: 'none', ...extra })

w.json.extensionsUsed.push('KHR_materials_unlit', 'VRMC_materials_mtoon', 'VRMC_vrm')
w.json.extensions.VRMC_vrm = {
  specVersion: '1.0',
  meta: {
    name: '艾拉',
    version: '1.0',
    authors: ['艾拉 aila 项目'],
    copyrightInformation: '原创形象，由 aila-desktop/scripts/avatar/make-avatar.mjs 生成',
    licenseUrl: 'https://vrm.dev/licenses/1.0/',
    avatarPermission: 'everyone',
    allowExcessivelyViolentUsage: false,
    allowExcessivelySexualUsage: false,
    commercialUsage: 'corporation',
    allowPoliticalOrReligiousUsage: false,
    allowAntisocialOrHateUsage: false,
    creditNotation: 'unnecessary',
    allowRedistribution: true,
    modification: 'allowModificationRedistribution'
  },
  humanoid: { humanBones: Object.fromEntries(boneNames.map((n) => [n, { node: boneIndex[n] }])) },
  firstPerson: { meshAnnotations: [] },
  lookAt: {
    offsetFromHeadBone: [0, 0.083, 0.09],
    type: 'bone',
    rangeMapHorizontalInner: { inputMaxValue: 90, outputScale: 9 },
    rangeMapHorizontalOuter: { inputMaxValue: 90, outputScale: 9 },
    rangeMapVerticalDown: { inputMaxValue: 90, outputScale: 7 },
    rangeMapVerticalUp: { inputMaxValue: 90, outputScale: 7 }
  },
  expressions: {
    preset: {
      happy: expr([...eyes('blink', 0.62), ...bind('Mouth', 'smile')], { overrideBlink: 'blend' }),
      relaxed: expr([...eyes('blink', 0.35), ...bind('Mouth', 'smile', 0.5)]),
      sad: expr([...bind('Brow', 'sad'), ...bind('Mouth', 'frown'), ...eyes('blink', 0.18)]),
      surprised: expr([...bind('Brow', 'up'), ...eyes('wide'), ...bind('Mouth', 'oh', 0.7)]),
      angry: expr([...bind('Brow', 'angry'), ...bind('Mouth', 'frown', 0.6)]),
      blink: expr(eyes('blink')),
      aa: expr(bind('Mouth', 'aa')),
      ih: expr(bind('Mouth', 'ih')),
      ou: expr(bind('Mouth', 'ou')),
      ee: expr(bind('Mouth', 'ee')),
      oh: expr(bind('Mouth', 'oh'))
    }
  }
}

mkdirSync(dirname(OUT), { recursive: true })
const glb = w.toBuffer()
writeFileSync(OUT, glb)
const tris = w.json.accessors.filter((a, i) => w.json.meshes.some((m) => m.primitives[0].indices === i)).reduce((n, a) => n + a.count / 3, 0)
console.log(`写好了 ${OUT}：${(glb.length / 1024).toFixed(0)} KB，${w.json.meshes.length} 个网格，${tris} 个三角形，${boneNames.length} 根骨骼`)
