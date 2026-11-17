import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'fs'
import { tmpdir } from 'os'
import { join } from 'path'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { Drops, gitRoot } from './drops'

let root: string

beforeEach(() => {
  root = mkdtempSync(join(tmpdir(), 'aila-drop-'))
  mkdirSync(join(root, 'shop', '.git'), { recursive: true })
  mkdirSync(join(root, 'shop', 'app'), { recursive: true })
  writeFileSync(join(root, 'shop', 'app', 'cart.py'), 'def total(items):\n    return sum(items)\n')
  writeFileSync(join(root, 'shop', '.env'), 'KEY=1\n')
  writeFileSync(join(root, 'pic.png'), Buffer.from([137, 80, 78, 71, 0, 0, 1]))
  writeFileSync(join(root, 'gbk.txt'), Buffer.from([0xc4, 0xe3, 0xba, 0xc3])) // 「你好」的 GBK
})
afterEach(() => rmSync(root, { recursive: true, force: true }))

describe('拖进来的文件', () => {
  it('看一眼：在哪个仓库、是什么', () => {
    const d = new Drops()
    const [cart, env, pic, dir, gone] = d.inspect([
      join(root, 'shop', 'app', 'cart.py'),
      join(root, 'shop', '.env'),
      join(root, 'pic.png'),
      join(root, 'shop'),
      join(root, 'nope.txt')
    ])
    expect(cart).toMatchObject({ kind: 'text', name: 'cart.py', project: join(root, 'shop'), rel: 'app/cart.py' })
    expect(env.kind).toBe('secret')
    expect(pic.kind).toBe('binary')
    expect(dir).toMatchObject({ kind: 'dir', project: join(root, 'shop') })
    expect(gone.kind).toBe('missing')
    expect(gitRoot(join(root, 'pic.png'))).toBeNull()
  })

  it('只能读刚拖进来的文本文件，过期就不行', () => {
    let now = 0
    const d = new Drops(() => now)
    const cart = join(root, 'shop', 'app', 'cart.py')
    expect(d.read(cart)).toBeNull() // 没拖过
    d.inspect([cart, join(root, 'shop', '.env')])
    expect(d.read(cart)).toContain('def total')
    expect(d.read(join(root, 'shop', '.env'))).toBeNull() // 密钥文件不给读
    now = 11 * 60 * 1000
    expect(d.read(cart)).toBeNull()
  })

  it('GBK 的老文件也读得出来', () => {
    const d = new Drops()
    const p = join(root, 'gbk.txt')
    d.inspect([p])
    expect(d.read(p)).toBe('你好')
  })
})
