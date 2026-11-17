/**
 * 拖给她的文件、剪贴板里的文字：哪些能看、怎么交给她。纯逻辑，主进程和角色窗口共用，有测试。
 *
 * - 看起来存着密钥的文件（.env、私钥……）不看：和编程 Agent 的规则一样（aila-ai runtime/workspace.py 的 SECRET_NAME）
 * - 图片、压缩包这类二进制、太大的文件不看（DeepSeek 不能看图）
 * - 内容会发给大模型，所以发之前把常见的密钥写法打码（和看屏幕的规则一样：aila-ai screen/detect.py 的 SECRETS）
 * - 对话的一句话最多 8000 字（AI 服务的限制），文件内容只放前 MAX_CONTENT 字
 */

export type DropKind = 'text' | 'binary' | 'too-big' | 'secret' | 'dir' | 'missing'

export interface DropInfo {
  path: string
  name: string
  kind: DropKind
  size: number
  /** 在哪个 git 仓库里（找得到的话）：可以交给任务页去处理 */
  project?: string
  /** 相对那个仓库的路径 */
  rel?: string
}

/** 最多看多大的文件 */
export const MAX_FILE_BYTES = 200 * 1024
/** 发给她的文件内容、剪贴板内容最多多少字 */
export const MAX_CONTENT = 6000

const SECRET_NAME = /(^\.env(\..+)?$|\.pem$|\.key$|\.pfx$|\.p12$|^id_(rsa|dsa|ecdsa|ed25519)(\.pub)?$|credentials|secrets?\.(json|ya?ml|toml)$)/i
const SAFE_ENV_EXAMPLES = /^\.env\.(example|sample|template)$/i

export function isSecretName(name: string): boolean {
  return SECRET_NAME.test(name) && !SAFE_ENV_EXAMPLES.test(name)
}

/** 文件开头有 NUL 字节、或者不可见字符太多：当二进制 */
export function looksBinary(head: Uint8Array): boolean {
  if (!head.length) return false
  let odd = 0
  for (const b of head) {
    if (b === 0) return true
    if (b < 9 || (b > 13 && b < 32)) odd++
  }
  return odd / head.length > 0.1
}

export function classify(name: string, size: number, head: Uint8Array): DropKind {
  if (isSecretName(name)) return 'secret'
  if (size > MAX_FILE_BYTES) return 'too-big'
  if (looksBinary(head)) return 'binary'
  return 'text'
}

const SECRETS: [RegExp, string][] = [
  [/\bbearer\s+\S+/gi, 'Bearer ***'],
  [/\bsk-[A-Za-z0-9_-]{12,}/g, 'sk-***'],
  [/\b(authorization|cookie)(\s*:\s*).+/gi, '$1$2***'],
  [/\b(api[_-]?key|access[_-]?key|token|secret|password|passwd|pwd)(\s*[:=]\s*)(\S+)/gi, '$1$2***'],
  // 很长的一串、字母数字混着的（令牌、密钥）；不含 / 和 .，路径、网址不受影响
  [/\b(?=[A-Za-z0-9+_-]*\d)(?=[A-Za-z0-9+_-]*[A-Za-z])[A-Za-z0-9+_-]{32,}={0,2}/g, '***']
]

export function redact(text: string): string {
  return SECRETS.reduce((t, [re, repl]) => t.replace(re, repl), text)
}

function cap(text: string): { text: string; cut: boolean } {
  return text.length > MAX_CONTENT ? { text: text.slice(0, MAX_CONTENT), cut: true } : { text, cut: false }
}

/** 拖给她的文件：「讲讲它」时发给她的那句话 */
export function fileMessage(name: string, content: string): string {
  const { text, cut } = cap(redact(content))
  return `帮我看看这个文件「${name}」，说说它是做什么的，有没有明显的问题。${cut ? `（太长了，只给了前 ${MAX_CONTENT} 字）` : ''}\n\`\`\`\n${text}\n\`\`\``
}

/** 拖给她的文件：「交给任务页」时开的任务 */
export function fileTask(rel: string): string {
  return `看看 ${rel}：先读一下，说说它是做什么的、有没有明显的问题。先别改，等我说。`
}

/** 说的话里提到剪贴板（「看看我剪贴板里的报错」） */
export function mentionsClipboard(text: string): boolean {
  return /剪[贴切]板|粘贴板|clipboard/i.test(text)
}

/** 带上剪贴板内容的那句话；剪贴板空的返回 null */
export function clipboardMessage(said: string, clip: string): string | null {
  const trimmed = clip.trim()
  if (!trimmed) return null
  const { text, cut } = cap(redact(trimmed))
  return `${said}\n\n（剪贴板里的内容${cut ? `，太长只给了前 ${MAX_CONTENT} 字` : ''}，密钥已打码）\n\`\`\`\n${text}\n\`\`\``
}
