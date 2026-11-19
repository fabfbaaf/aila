/**
 * 读形象文件（主进程）：VRM 就是一个文件；Live2D 是 .model3.json 所在的整个文件夹（贴图、动作、物理……都按相对路径引用）。
 * 只读这个文件夹里面的普通文件，不跟着链接走，文件数和总大小有上限。
 */
import { lstatSync, readdirSync, readFileSync, existsSync } from 'node:fs'
import { basename, dirname, join, relative } from 'node:path'
import { LIVE2D_MAX_BYTES, LIVE2D_MAX_FILES, modelKind, relativeModelPath, type LoadedModel, type ModelFiles } from '../shared/model'

function toArrayBuffer(buf: Buffer): ArrayBuffer {
  return buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength) as ArrayBuffer
}

export function readLive2DFolder(model3: string): ModelFiles | null {
  const folder = dirname(model3)
  const name = basename(folder)
  const files: ModelFiles['files'] = []
  let total = 0
  const walk = (dir: string): boolean => {
    for (const entry of readdirSync(dir)) {
      const full = join(dir, entry)
      const st = lstatSync(full)
      if (st.isSymbolicLink()) continue
      if (st.isDirectory()) {
        if (!walk(full)) return false
        continue
      }
      if (!st.isFile()) continue
      const rel = relativeModelPath(name, relative(folder, full))
      if (!rel) continue
      total += st.size
      if (files.length >= LIVE2D_MAX_FILES || total > LIVE2D_MAX_BYTES) return false
      files.push({ path: rel, data: toArrayBuffer(readFileSync(full)) })
    }
    return true
  }
  if (!walk(folder)) return null
  const entry = relativeModelPath(name, basename(model3))
  return entry ? { entry, files } : null
}

/** 按扩展名读：.vrm 或 .model3.json；读不了、太大、不认识都返回 null */
export function loadModelFile(path: string | undefined): LoadedModel | null {
  if (!path || !existsSync(path)) return null
  try {
    const kind = modelKind(path)
    if (kind === 'vrm') return { kind, data: toArrayBuffer(readFileSync(path)) }
    if (kind === 'live2d') {
      const model = readLive2DFolder(path)
      return model ? { kind, model } : null
    }
  } catch {
    // 读到一半文件没了、没权限
  }
  return null
}
