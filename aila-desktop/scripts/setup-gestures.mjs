// 手势识别要用的文件：MediaPipe 的 WebAssembly（从 node_modules 复制）和手部关键点模型（从 MediaPipe 官方下载，约 7.5MB）
// 放在 src/renderer/public/mediapipe/，开发和打包后都从本地加载，不联网；这个目录不进 Git
// 用法：npm run setup:gestures
import { copyFile, mkdir, rename, stat, writeFile } from 'node:fs/promises'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const target = join(root, 'src', 'renderer', 'public', 'mediapipe')
const wasm = join(root, 'node_modules', '@mediapipe', 'tasks-vision', 'wasm')
const MODEL_URL = 'https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task'

await mkdir(target, { recursive: true })
for (const name of ['vision_wasm_internal.js', 'vision_wasm_internal.wasm', 'vision_wasm_nosimd_internal.js', 'vision_wasm_nosimd_internal.wasm']) {
  await copyFile(join(wasm, name), join(target, name))
}
console.log('已复制 MediaPipe 的 WebAssembly')

const model = join(target, 'hand_landmarker.task')
const exists = await stat(model).then((s) => s.size > 1_000_000).catch(() => false)
if (exists) {
  console.log('手部模型已经有了')
} else {
  console.log('下载手部模型……', MODEL_URL)
  const res = await fetch(MODEL_URL)
  if (!res.ok) throw new Error(`下载失败：HTTP ${res.status}`)
  const data = Buffer.from(await res.arrayBuffer())
  await writeFile(model + '.part', data)
  await rename(model + '.part', model)
  console.log(`手部模型下载完成（${(data.length / 1024 / 1024).toFixed(1)} MB）`)
}
