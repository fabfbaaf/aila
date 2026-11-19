// 生成安装包和 exe 的图标 build/icon.ico：和托盘一样的紫色圆环（src/main/trayIcon.ts 的画法）。
//
// 为什么用代码画：仓库里不放手画的二进制图标，改颜色、改样子改这里重新生成就行。
// ICO 里放 16/24/32/48/64/128/256 七个尺寸的 PNG（Windows Vista 以后都认 PNG 格式的 ICO）。
// 用法：node scripts/make-icon.mjs（npm run dist 会先跑它）
import { mkdirSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { deflateSync } from 'node:zlib'

const COLOR = { r: 0xa7, g: 0x8b, b: 0xfa } // #a78bfa，和主窗口的强调色一样
const SIZES = [16, 24, 32, 48, 64, 128, 256]

/** 和 trayIcon.ts 同样的圆环，但输出不预乘的 RGBA（PNG 要的） */
function ring(size) {
  const buf = Buffer.alloc(size * size * 4)
  const c = (size - 1) / 2
  const outer = size / 2 - 0.5
  const inner = size * 0.16
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const d = Math.hypot(x - c, y - c)
      let a = Math.max(0, Math.min(1, outer - d + 0.5))
      a *= Math.max(0, Math.min(1, d - inner + 0.5))
      const i = (y * size + x) * 4
      buf[i] = COLOR.r
      buf[i + 1] = COLOR.g
      buf[i + 2] = COLOR.b
      buf[i + 3] = Math.round(255 * a)
    }
  }
  return buf
}

const CRC_TABLE = Array.from({ length: 256 }, (_, n) => {
  let c = n
  for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1
  return c >>> 0
})

function crc32(data) {
  let c = 0xffffffff
  for (const byte of data) c = CRC_TABLE[(c ^ byte) & 0xff] ^ (c >>> 8)
  return (c ^ 0xffffffff) >>> 0
}

function chunk(type, data) {
  const len = Buffer.alloc(4)
  len.writeUInt32BE(data.length)
  const body = Buffer.concat([Buffer.from(type, 'ascii'), data])
  const crc = Buffer.alloc(4)
  crc.writeUInt32BE(crc32(body))
  return Buffer.concat([len, body, crc])
}

function png(size) {
  const rgba = ring(size)
  // 每行前面一个字节的过滤方式（0 = 不过滤）
  const raw = Buffer.alloc((size * 4 + 1) * size)
  for (let y = 0; y < size; y++) rgba.copy(raw, y * (size * 4 + 1) + 1, y * size * 4, (y + 1) * size * 4)
  const ihdr = Buffer.alloc(13)
  ihdr.writeUInt32BE(size, 0)
  ihdr.writeUInt32BE(size, 4)
  ihdr[8] = 8 // 每个通道 8 位
  ihdr[9] = 6 // RGBA
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk('IHDR', ihdr),
    chunk('IDAT', deflateSync(raw, { level: 9 })),
    chunk('IEND', Buffer.alloc(0))
  ])
}

export function ico(sizes = SIZES) {
  const images = sizes.map(png)
  const header = Buffer.alloc(6)
  header.writeUInt16LE(0, 0)
  header.writeUInt16LE(1, 2) // 1 = 图标
  header.writeUInt16LE(sizes.length, 4)
  let offset = 6 + 16 * sizes.length
  const entries = sizes.map((size, i) => {
    const e = Buffer.alloc(16)
    e[0] = size >= 256 ? 0 : size // 0 表示 256
    e[1] = size >= 256 ? 0 : size
    e.writeUInt16LE(1, 4) // 色彩平面
    e.writeUInt16LE(32, 6) // 每像素位数
    e.writeUInt32LE(images[i].length, 8)
    e.writeUInt32LE(offset, 12)
    offset += images[i].length
    return e
  })
  return Buffer.concat([header, ...entries, ...images])
}

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const out = join(root, 'build', 'icon.ico')
mkdirSync(dirname(out), { recursive: true })
writeFileSync(out, ico())
console.log(`图标：${out}`)
