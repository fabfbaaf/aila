/**
 * 她的形象：VRM 模型（一个 .vrm 文件）或者 Live2D 模型（一个 .model3.json 加同一个文件夹里的贴图、动作、物理……）。
 * 主进程读文件（渲染层碰不到磁盘），把内容交给角色窗口。
 */

/** Live2D 模型文件夹里的文件：path 是带文件夹名的相对路径（比如 hiyori/hiyori.model3.json） */
export interface ModelFiles {
  entry: string
  files: { path: string; data: ArrayBuffer }[]
}

export type LoadedModel = { kind: 'vrm'; data: ArrayBuffer } | { kind: 'live2d'; model: ModelFiles }

/** 一个 Live2D 模型最多读多少文件、多大（官方示例模型几 MB 到几十 MB） */
export const LIVE2D_MAX_FILES = 500
export const LIVE2D_MAX_BYTES = 200 * 1024 * 1024

export type ModelKind = 'vrm' | 'live2d'

/** 按文件名认模型种类；都不是返回 null */
export function modelKind(path: string): ModelKind | null {
  const p = path.toLowerCase()
  if (p.endsWith('.vrm')) return 'vrm'
  if (p.endsWith('.model3.json')) return 'live2d'
  return null
}

/** 模型文件夹里的一个文件 → 带文件夹名的相对路径（统一用 /）；跑到文件夹外面的（..）返回 null */
export function relativeModelPath(folderName: string, rel: string): string | null {
  const parts = rel.replace(/\\/g, '/').split('/').filter(Boolean)
  if (parts.some((p) => p === '..') || parts.length === 0) return null
  return [folderName, ...parts].join('/')
}
