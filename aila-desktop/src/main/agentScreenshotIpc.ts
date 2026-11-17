/** 任务截图只从 AI 数据目录下对应会话的截图目录读取。渲染层只收到 PNG，不接触磁盘路径。 */
import type { IpcMain, WebContents } from 'electron'
import { lstat, open, realpath } from 'node:fs/promises'
import { isAbsolute, join, relative } from 'node:path'
import { isAgentScreenshotName } from '../shared/agent'
import type { AiResult } from '../shared/settings'

const SESSION_ID = /^\d{8}-\d{6}-[0-9a-f]{6}$/
const MAX_SCREENSHOT_BYTES = 16 * 1024 * 1024
const PNG_HEADER = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])

async function regularDirectory(path: string): Promise<void> {
  if (!(await lstat(path)).isDirectory() || relative(path, await realpath(path)) !== '') {
    throw new Error('截图目录不在会话里。')
  }
}

async function readScreenshot(root: string, sid: string, name: string): Promise<string> {
  if (!isAbsolute(root)) throw new Error('AI 数据目录没有准备好。')
  const sessions = join(await realpath(root), 'sessions')
  const dir = join(sessions, `${sid}.shots`)
  await regularDirectory(sessions)
  await regularDirectory(dir)

  const path = join(dir, name)
  const expected = await lstat(path)
  if (!expected.isFile() || expected.nlink !== 1 || relative(path, await realpath(path)) !== '') {
    throw new Error('截图路径无效。')
  }
  const file = await open(path, 'r')
  try {
    const stat = await file.stat()
    if (!stat.isFile() || stat.nlink !== 1 || stat.dev !== expected.dev || stat.ino !== expected.ino) {
      throw new Error('截图文件变了。')
    }
    await regularDirectory(dir)
    if (stat.size < PNG_HEADER.length || stat.size > MAX_SCREENSHOT_BYTES) {
      throw new Error('截图大小无效。')
    }
    const bytes = Buffer.alloc(stat.size)
    let offset = 0
    while (offset < bytes.length) {
      const { bytesRead } = await file.read(bytes, offset, bytes.length - offset, offset)
      if (!bytesRead) throw new Error('截图文件不完整。')
      offset += bytesRead
    }
    if (!bytes.subarray(0, PNG_HEADER.length).equals(PNG_HEADER)) {
      throw new Error('截图格式无效。')
    }
    return `data:image/png;base64,${bytes.toString('base64')}`
  } finally {
    await file.close()
  }
}

export function registerAgentScreenshotIpc(
  ipc: Pick<IpcMain, 'handle'>,
  fromMain: (event: { sender: WebContents }) => boolean,
  dataDir: () => Promise<string | undefined>
): void {
  ipc.handle('agent:screenshot', async (event, sid: unknown, name: unknown): Promise<AiResult<string>> => {
    if (!fromMain(event)) return { ok: false, error: '只有主窗口能用' }
    if (typeof sid !== 'string' || sid.length !== 22 || !SESSION_ID.test(sid) || !isAgentScreenshotName(name)) {
      return { ok: false, error: '截图路径无效。' }
    }
    try {
      const root = await dataDir()
      if (!root) return { ok: false, error: 'AI 数据目录还没准备好。' }
      return { ok: true, data: await readScreenshot(root, sid, name) }
    } catch {
      return { ok: false, error: '截图读不到了。' }
    }
  })
}
