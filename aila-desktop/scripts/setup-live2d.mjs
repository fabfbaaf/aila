// 装 Live2D 运行库（Cubism Core for Web）：npm run setup:live2d -- --accept-license
//
// 为什么不放进仓库：Cubism Core 是 Live2D 公司的专有软件，只能在同意它的许可协议以后使用、随应用分发，
// 不能放进开源仓库。这个脚本先把许可协议的地址给你看，你加上 --accept-license（表示你读过并同意）才下载。
// 下载到 src/renderer/public/live2d/（不进 Git）；npm run dist 打安装包时会一起带上。
// 只用 VRM 形象的话不用装。
import { createHash } from 'node:crypto'
import { mkdirSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const URL_CORE = 'https://cubism.live2d.com/sdk-web/cubismcore/live2dcubismcore.min.js'
const LICENSE = 'https://www.live2d.com/eula/live2d-proprietary-software-license-agreement_en.html'
const PUBLICATION = 'https://www.live2d.com/en/sdk/license/'
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const out = join(root, 'src', 'renderer', 'public', 'live2d', 'live2dcubismcore.min.js')

console.log(`Live2D 运行库（Cubism Core for Web）是 Live2D 公司的专有软件，用之前要同意它的许可协议：
  ${LICENSE}
用在商业产品里（年收入超过一定规模）还要和 Live2D 签出版许可：
  ${PUBLICATION}
`)

if (!process.argv.includes('--accept-license')) {
  console.log('读过并同意的话，运行：npm run setup:live2d -- --accept-license')
  process.exit(1)
}

const res = await fetch(URL_CORE)
if (!res.ok) {
  console.error(`下载失败（HTTP ${res.status}）：${URL_CORE}`)
  process.exit(1)
}
const data = Buffer.from(await res.arrayBuffer())
if (!data.toString('utf8', 0, 4000).includes('Live2DCubismCore')) {
  console.error('下载下来的不像是 Cubism Core，没保存。')
  process.exit(1)
}
mkdirSync(dirname(out), { recursive: true })
writeFileSync(out, data)
console.log(`装好了：${out}（${(data.length / 1024).toFixed(0)}KB，sha256 ${createHash('sha256').update(data).digest('hex').slice(0, 16)}…）`)
console.log('在设置 →「形象」里选 Live2D 模型的 .model3.json 就能用。')
