import { describe, expect, it, vi } from 'vitest'

vi.mock('electron', () => ({ BrowserWindow: class {}, session: {} }))

const { droppedPathOk } = await import('./extensions')

const dir = { isDirectory: () => true, isFile: () => false }
const file = { isDirectory: () => false, isFile: () => true }

describe('拖进来的扩展包', () => {
  it('只认本机上真有的文件夹或 .zip', () => {
    expect(droppedPathOk('C:/x/skills', () => dir)).toBe(true)
    expect(droppedPathOk('C:/x/pack.ZIP', () => file)).toBe(true)
    expect(droppedPathOk('C:/x/run.exe', () => file)).toBe(false)
    expect(droppedPathOk('C:/x/nope', () => null)).toBe(false)
  })

  it('相对路径、空的、不是文字的都不要', () => {
    expect(droppedPathOk('pack.zip', () => file)).toBe(false)
    expect(droppedPathOk('', () => dir)).toBe(false)
    expect(droppedPathOk(42, () => dir)).toBe(false)
    expect(droppedPathOk('C:/' + 'a'.repeat(1200), () => dir)).toBe(false)
  })
})
