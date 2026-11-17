/**
 * 拖到她身上的文件：主进程看一眼（多大、是不是文本、是不是密钥文件、在哪个 git 仓库里），
 * 角色窗口只能读「刚刚拖进来、看过一眼」的文件——不能让渲染层随便指定路径读本机的文件。
 */

import { closeSync, existsSync, openSync, readFileSync, readSync, statSync } from 'fs'
import { basename, dirname, join, relative } from 'path'
import { classify, MAX_FILE_BYTES, type DropInfo } from '../shared/drop'

/** 拖进来以后多久内可以读 */
const ALLOW_MS = 10 * 60 * 1000
const HEAD_BYTES = 8192

/** 往上找 .git（目录或者 worktree 的 .git 文件），找到的就是项目根目录 */
export function gitRoot(path: string): string | null {
  let dir = path
  for (let i = 0; i < 40; i++) {
    if (existsSync(join(dir, '.git'))) return dir
    const up = dirname(dir)
    if (up === dir) return null
    dir = up
  }
  return null
}

export class Drops {
  private allowed = new Map<string, number>()

  constructor(private now: () => number = Date.now) {}

  inspect(paths: string[]): DropInfo[] {
    return paths.slice(0, 10).map((p) => this.one(p))
  }

  private one(path: string): DropInfo {
    const name = basename(path)
    let size = 0
    try {
      const st = statSync(path)
      size = st.size
      if (st.isDirectory()) {
        const project = gitRoot(path) ?? path
        return { path, name, kind: 'dir', size: 0, project, rel: relative(project, path).replace(/\\/g, '/') || '.' }
      }
    } catch {
      return { path, name, kind: 'missing', size: 0 }
    }
    const head = this.head(path)
    const kind = classify(name, size, head)
    const root = gitRoot(dirname(path))
    if (kind === 'text') this.allowed.set(path, this.now() + ALLOW_MS)
    return { path, name, kind, size, ...(root ? { project: root, rel: relative(root, path).replace(/\\/g, '/') } : {}) }
  }

  private head(path: string): Uint8Array {
    const buf = Buffer.alloc(HEAD_BYTES)
    const fd = openSync(path, 'r')
    try {
      const n = readSync(fd, buf, 0, HEAD_BYTES, 0)
      return buf.subarray(0, n)
    } finally {
      closeSync(fd)
    }
  }

  /** 读刚拖进来的文本文件；没拖过、过期了、变大了的返回 null */
  read(path: string): string | null {
    const until = this.allowed.get(path)
    if (until === undefined || until < this.now()) {
      this.allowed.delete(path)
      return null
    }
    try {
      if (statSync(path).size > MAX_FILE_BYTES) return null
      const data = readFileSync(path)
      // 先当 UTF-8；解不开（中文 Windows 上的老文件）再试 GBK
      try {
        return new TextDecoder('utf-8', { fatal: true }).decode(data).replace(/^﻿/, '')
      } catch {
        return new TextDecoder('gbk').decode(data)
      }
    } catch {
      return null
    }
  }
}
