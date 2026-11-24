// 生成艾拉的图标：「艾拉」两个字（志莽行书）写在深色圆角方块上，和主窗口侧栏的标志一样。
//
// 为什么在 Electron 里画：字形要用真字体排出来，Electron 自带 Chromium，离屏窗口排好字截图就是 PNG，不用另装画图的包。
// 字体只有「艾拉」两个字的子集（src/renderer/src/theme/fonts，OFL 授权），内嵌成 data: 地址，不碰网络。
// 小尺寸（≤32）只写一个「艾」，两个字在托盘那么小认不出来。
//
// 输出（生成以后提交进仓库，打包时不再重新生成）：
// - build/icon.ico：安装包和 exe 的图标，16/24/32/48/64/128/256 七个尺寸的 PNG
// - build/logo.png：512 的大图，README 用
// - src/main/trayImage.ts：托盘图标 16 和 32 两个尺寸（base64），主进程直接用
//
// 用法：npm run icon（就是 electron scripts/make-icon.mjs）
import { app, BrowserWindow } from 'electron'
import { readFileSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const FONT = join(root, 'src', 'renderer', 'src', 'theme', 'fonts', 'ZhiMangXing-aila.woff2')
const ICO_SIZES = [16, 24, 32, 48, 64, 128, 256]

app.commandLine.appendSwitch('force-device-scale-factor', '1')
app.disableHardwareAcceleration()

/** 一个尺寸的图标：深色圆角方块 + 渐变色的字（放在左上角，截图只截这一块） */
export function tileHtml(size) {
  const text = size <= 32 ? '艾' : '艾拉'
  const fontSize = size <= 32 ? size * 0.78 : size * 0.44
  return `<div class="tile" style="width:${size}px;height:${size}px;border-radius:${Math.round(size * 0.23)}px">` +
    `<span class="t" style="font-size:${fontSize}px;padding-bottom:${size * 0.03}px">${text}</span></div>`
}

const PAGE = (fontDataUrl) => `<!doctype html><meta charset="utf-8"><style>
@font-face { font-family: Brand; src: url(${fontDataUrl}) format('woff2'); }
html, body { margin: 0; background: transparent; overflow: hidden; }
.tile { box-sizing: border-box; display: flex; align-items: center; justify-content: center;
  background: radial-gradient(circle at 50% 30%, #33285c 0%, #1a1430 55%, #110d1f 100%); }
.t { font-family: Brand; line-height: 1; white-space: nowrap;
  background: linear-gradient(160deg, #f1ecff 15%, #c4b5fd 55%, #67e8f9 110%);
  -webkit-background-clip: text; background-clip: text; color: transparent; }
</style><body id="b"><span style="font-family:Brand;position:absolute;left:-999px">艾拉</span></body>`

/** 一个离屏窗口画所有尺寸：换掉内容、等一帧、截左上角那一块 */
async function renderer(fontDataUrl) {
  const win = new BrowserWindow({ width: 600, height: 600, show: false, frame: false, transparent: true,
    webPreferences: { offscreen: true, sandbox: true, contextIsolation: true } })
  await win.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(PAGE(fontDataUrl)))
  const ok = await win.webContents.executeJavaScript('document.fonts.ready.then(() => document.fonts.check("12px Brand", "艾拉"))')
  if (!ok) throw new Error('字体没加载上')
  return {
    async png(size) {
      await win.webContents.executeJavaScript(`document.getElementById('b').innerHTML = ${JSON.stringify(tileHtml(size))};
        new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))`)
      await new Promise((r) => setTimeout(r, 120))
      let image = await win.webContents.capturePage({ x: 0, y: 0, width: size, height: size })
      if (image.getSize().width !== size) image = image.resize({ width: size, height: size, quality: 'best' })
      return image.toPNG()
    },
    close: () => win.destroy()
  }
}

function ico(images) {
  const header = Buffer.alloc(6)
  header.writeUInt16LE(0, 0)
  header.writeUInt16LE(1, 2) // 1 = 图标
  header.writeUInt16LE(images.length, 4)
  let offset = 6 + 16 * images.length
  const entries = images.map(({ size, png }) => {
    const e = Buffer.alloc(16)
    e[0] = size >= 256 ? 0 : size // 0 表示 256
    e[1] = size >= 256 ? 0 : size
    e.writeUInt16LE(1, 4) // 色彩平面
    e.writeUInt16LE(32, 6) // 每像素位数
    e.writeUInt32LE(png.length, 8)
    e.writeUInt32LE(offset, 12)
    offset += png.length
    return e
  })
  return Buffer.concat([header, ...entries, ...images.map((i) => i.png)])
}

app.whenReady().then(async () => {
  try {
    const fontDataUrl = 'data:font/woff2;base64,' + readFileSync(FONT).toString('base64')
    const draw = await renderer(fontDataUrl)
    const images = []
    for (const size of ICO_SIZES) images.push({ size, png: await draw.png(size) })
    writeFileSync(join(root, 'build', 'icon.ico'), ico(images))
    writeFileSync(join(root, 'build', 'logo.png'), await draw.png(512))
    draw.close()
    const tray = (size) => images.find((i) => i.size === size).png.toString('base64')
    writeFileSync(join(root, 'src', 'main', 'trayImage.ts'), [
      '// 托盘图标（scripts/make-icon.mjs 生成，不要手改）：16 和 32 像素的 PNG，和安装包图标同一个样子',
      `export const TRAY_16 = '${tray(16)}'`,
      `export const TRAY_32 = '${tray(32)}'`,
      ''
    ].join('\n'))
    console.log('图标：build/icon.ico、build/logo.png、src/main/trayImage.ts')
  } catch (err) {
    console.error('生成图标失败：', err)
    process.exitCode = 1
  }
  app.quit()
})
