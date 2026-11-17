// 把一组「蒙皮网格 + 骨骼 + 材质 + VRM 扩展」写成 .glb（glTF 2.0 二进制）。
// 只用到这个项目需要的那一小部分：每个顶点只绑一根骨骼（刚性蒙皮）、没有贴图、形变目标只有 POSITION。

const FLOAT = 5126
const USHORT = 5123
const UINT = 5125
const ARRAY_BUFFER = 34962
const ELEMENT_ARRAY_BUFFER = 34963

export class GlbWriter {
  constructor() {
    this.json = {
      asset: { version: '2.0', generator: 'aila make-avatar' },
      scene: 0,
      scenes: [{ nodes: [] }],
      nodes: [],
      meshes: [],
      materials: [],
      accessors: [],
      bufferViews: [],
      buffers: [{ byteLength: 0 }],
      skins: [],
      extensionsUsed: [],
      extensions: {}
    }
    this.chunks = []
    this.length = 0
  }

  /** 加一块数据，返回 bufferView 下标（4 字节对齐） */
  view(typed, target) {
    const bytes = new Uint8Array(typed.buffer, typed.byteOffset, typed.byteLength)
    const pad = (4 - (this.length % 4)) % 4
    if (pad) {
      this.chunks.push(new Uint8Array(pad))
      this.length += pad
    }
    const index = this.json.bufferViews.length
    this.json.bufferViews.push({ buffer: 0, byteOffset: this.length, byteLength: bytes.byteLength, ...(target ? { target } : {}) })
    this.chunks.push(bytes)
    this.length += bytes.byteLength
    return index
  }

  accessor(typed, type, componentType, target, withBounds = false) {
    const size = { SCALAR: 1, VEC2: 2, VEC3: 3, VEC4: 4, MAT4: 16 }[type]
    const acc = { bufferView: this.view(typed, target), componentType, count: typed.length / size, type }
    if (withBounds) {
      const min = new Array(size).fill(Infinity)
      const max = new Array(size).fill(-Infinity)
      for (let i = 0; i < typed.length; i++) {
        const k = i % size
        if (typed[i] < min[k]) min[k] = typed[i]
        if (typed[i] > max[k]) max[k] = typed[i]
      }
      acc.min = min
      acc.max = max
    }
    this.json.accessors.push(acc)
    return this.json.accessors.length - 1
  }

  node(n) {
    this.json.nodes.push(n)
    return this.json.nodes.length - 1
  }

  material(m) {
    this.json.materials.push(m)
    return this.json.materials.length - 1
  }

  /** 一个网格：position/normal/uv/joints/weights/indices，morphs = [{ name, delta: Float32Array }] */
  mesh(name, data, material, skin, morphs = []) {
    const attributes = {
      POSITION: this.accessor(data.position, 'VEC3', FLOAT, ARRAY_BUFFER, true),
      NORMAL: this.accessor(data.normal, 'VEC3', FLOAT, ARRAY_BUFFER),
      TEXCOORD_0: this.accessor(data.uv, 'VEC2', FLOAT, ARRAY_BUFFER),
      JOINTS_0: this.accessor(data.joints, 'VEC4', USHORT, ARRAY_BUFFER),
      WEIGHTS_0: this.accessor(data.weights, 'VEC4', FLOAT, ARRAY_BUFFER)
    }
    const big = data.position.length / 3 > 65535
    const indices = this.accessor(big ? new Uint32Array(data.index) : new Uint16Array(data.index), 'SCALAR', big ? UINT : USHORT, ELEMENT_ARRAY_BUFFER)
    const primitive = { attributes, indices, material }
    const mesh = { name, primitives: [primitive] }
    if (morphs.length) {
      primitive.targets = morphs.map((m) => ({ POSITION: this.accessor(m.delta, 'VEC3', FLOAT, ARRAY_BUFFER, true) }))
      mesh.weights = morphs.map(() => 0)
      mesh.extras = { targetNames: morphs.map((m) => m.name) }
    }
    this.json.meshes.push(mesh)
    const node = this.node({ name, mesh: this.json.meshes.length - 1, skin })
    this.json.scenes[0].nodes.push(node)
    return node
  }

  toBuffer() {
    this.json.buffers[0].byteLength = this.length
    let bin = Buffer.concat(this.chunks.map((c) => Buffer.from(c.buffer, c.byteOffset, c.byteLength)))
    const binPad = (4 - (bin.length % 4)) % 4
    if (binPad) bin = Buffer.concat([bin, Buffer.alloc(binPad)])
    let text = Buffer.from(JSON.stringify(this.json), 'utf8')
    const jsonPad = (4 - (text.length % 4)) % 4
    if (jsonPad) text = Buffer.concat([text, Buffer.alloc(jsonPad, 0x20)])
    const header = Buffer.alloc(12)
    header.writeUInt32LE(0x46546c67, 0) // glTF
    header.writeUInt32LE(2, 4)
    header.writeUInt32LE(12 + 8 + text.length + 8 + bin.length, 8)
    const jsonHead = Buffer.alloc(8)
    jsonHead.writeUInt32LE(text.length, 0)
    jsonHead.writeUInt32LE(0x4e4f534a, 4) // JSON
    const binHead = Buffer.alloc(8)
    binHead.writeUInt32LE(bin.length, 0)
    binHead.writeUInt32LE(0x004e4942, 4) // BIN
    return Buffer.concat([header, jsonHead, text, binHead, bin])
  }
}
