import { describe, expect, it } from 'vitest'
import { classify, clipboardMessage, fileMessage, isSecretName, MAX_CONTENT, MAX_FILE_BYTES, mentionsClipboard, redact } from './drop'

const bytes = (s: string): Uint8Array => new TextEncoder().encode(s)

describe('拖给她的文件', () => {
  it('密钥文件不看（和编程 Agent 的规则一样），示例配置可以', () => {
    for (const n of ['.env', '.env.local', 'id_rsa', 'server.pem', 'credentials.json', 'secrets.yaml']) expect(isSecretName(n)).toBe(true)
    for (const n of ['.env.example', 'cart.py', 'README.md']) expect(isSecretName(n)).toBe(false)
  })

  it('分类：文本、二进制、太大、密钥', () => {
    expect(classify('cart.py', 100, bytes('def total(items):\n    return 1\n'))).toBe('text')
    expect(classify('a.png', 100, new Uint8Array([137, 80, 78, 71, 0, 1, 2]))).toBe('binary')
    expect(classify('big.log', MAX_FILE_BYTES + 1, bytes('x'))).toBe('too-big')
    expect(classify('.env', 10, bytes('A=1'))).toBe('secret')
    expect(classify('中文.md', 10, bytes('你好，世界'))).toBe('text')
  })

  it('发给她之前打码、截断', () => {
    const msg = fileMessage('cfg.py', 'API_KEY = "sk-abcdefghijklmnop1234"\npassword=hunter2\n')
    expect(msg).not.toContain('abcdefghijklmnop')
    expect(msg).not.toContain('hunter2')
    expect(msg).toContain('cfg.py')
    const long = fileMessage('a.txt', 'x'.repeat(MAX_CONTENT + 100))
    expect(long).toContain(`只给了前 ${MAX_CONTENT} 字`)
    expect(long.length).toBeLessThan(8000) // AI 服务一句话最多 8000 字
  })
})

describe('剪贴板', () => {
  it('认得「剪贴板」的几种说法', () => {
    expect(mentionsClipboard('看看我剪贴板里的报错')).toBe(true)
    expect(mentionsClipboard('剪切板里那段')).toBe(true)
    expect(mentionsClipboard('今天天气怎么样')).toBe(false)
  })

  it('带上内容、打码；空的返回 null', () => {
    const msg = clipboardMessage('看看我剪贴板里的报错', 'Traceback...\nAuthorization: Bearer abc.def.ghi\n')!
    expect(msg.startsWith('看看我剪贴板里的报错')).toBe(true)
    expect(msg).toContain('Traceback')
    expect(msg).not.toContain('abc.def.ghi')
    expect(clipboardMessage('看看', '   ')).toBeNull()
  })

  it('打码不动路径和网址', () => {
    expect(redact('File "C:/work/shop/app/cart_service_implementation.py", line 3')).toContain('cart_service_implementation.py')
    expect(redact('https://example.com/a/b')).toBe('https://example.com/a/b')
  })
})
