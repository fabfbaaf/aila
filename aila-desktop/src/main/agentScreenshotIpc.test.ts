/** 用临时目录核对截图 IPC 的窗口来源、会话路径和文件身份。 */
import { link, mkdir, mkdtemp, rm, symlink, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { registerAgentScreenshotIpc } from './agentScreenshotIpc'

const SID = '20261117-123456-abcdef'
const PNG_NAME = '1234567890abcdef1234567890abcdef.png'
const PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jqh0AAAAASUVORK5CYII='
const main = {}
let root: string

beforeEach(async () => { root = await mkdtemp(join(tmpdir(), 'aila-shot-')) })
afterEach(async () => { await rm(root, { recursive: true, force: true }) })

function handler() {
  const callbacks = new Map<string, (...args: any[]) => unknown>()
  const data = vi.fn(async () => root)
  registerAgentScreenshotIpc({ handle: (name, callback) => { callbacks.set(name, callback) } },
    (event) => event.sender === main, data)
  return { read: callbacks.get('agent:screenshot')!, data }
}

describe('浏览器截图 IPC', () => {
  it('只接受主窗口和会话内的 PNG 文件名', async () => {
    const { read, data } = handler()
    expect(await read({ sender: {} }, SID, PNG_NAME)).toMatchObject({ ok: false, error: '只有主窗口能用' })
    expect(await read({ sender: main }, '../章节', PNG_NAME)).toMatchObject({ ok: false })
    for (const name of ['../' + PNG_NAME, '/outside.png', 'a.png', PNG_NAME + '\n', PNG_NAME + ':stream']) {
      expect(await read({ sender: main }, SID, name)).toMatchObject({ ok: false })
    }
    expect(data).not.toHaveBeenCalled()
    const shots = join(root, 'sessions', `${SID}.shots`)
    await mkdir(shots, { recursive: true })
    const file = join(shots, PNG_NAME)
    await writeFile(file, Buffer.from(PNG, 'base64'))
    expect(await read({ sender: main }, SID, PNG_NAME)).toEqual({ ok: true, data: `data:image/png;base64,${PNG}` })
    await writeFile(file, '<svg>不是 PNG</svg>')
    expect(await read({ sender: main }, SID, PNG_NAME)).toMatchObject({ ok: false })
  })

  it('拒绝指向别的目录的截图目录和截图硬链接', async () => {
    const outside = join(root, 'outside')
    await mkdir(outside)
    await writeFile(join(outside, PNG_NAME), Buffer.from(PNG, 'base64'))
    const sessions = join(root, 'sessions')
    await mkdir(sessions)
    const shots = join(sessions, `${SID}.shots`)
    const { read } = handler()
    await symlink(outside, shots, process.platform === 'win32' ? 'junction' : 'dir')
    expect(await read({ sender: main }, SID, PNG_NAME)).toMatchObject({ ok: false })
    await rm(shots, { recursive: true })
    await mkdir(shots)
    await link(join(outside, PNG_NAME), join(shots, PNG_NAME))
    expect(await read({ sender: main }, SID, PNG_NAME)).toMatchObject({ ok: false })
  })
})
