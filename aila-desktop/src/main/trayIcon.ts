/**
 * 托盘图标：仓库里还没有图标文件（安装包那一期再做正式的），先用代码画一个紫色圆点，中间留一个小缺口像「艾」字的一点。
 * 返回 Electron nativeImage.createFromBitmap 要的 BGRA 像素，边缘做了抗锯齿。
 */

const COLOR = { b: 0xfa, g: 0x8b, r: 0xa7 } // #a78bfa，和主窗口的强调色一样

export function trayIcon(size: number): Buffer {
  const buf = Buffer.alloc(size * size * 4)
  const c = (size - 1) / 2
  const outer = size / 2 - 0.5
  const inner = size * 0.16
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const d = Math.hypot(x - c, y - c)
      // 外圈抗锯齿：离边缘一个像素以内按距离渐变
      let a = Math.max(0, Math.min(1, outer - d + 0.5))
      // 中间挖一个小圆（同样抗锯齿）
      a *= Math.max(0, Math.min(1, d - inner + 0.5))
      const i = (y * size + x) * 4
      // createFromBitmap 要预乘过透明度的颜色
      buf[i] = Math.round(COLOR.b * a)
      buf[i + 1] = Math.round(COLOR.g * a)
      buf[i + 2] = Math.round(COLOR.r * a)
      buf[i + 3] = Math.round(255 * a)
    }
  }
  return buf
}
