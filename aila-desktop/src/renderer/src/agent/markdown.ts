/**
 * 她说的话按 Markdown 显示。自己写一个小的：先把整段转义成纯文本，再只插入固定的几种标签，
 * 大模型的回复（可能被网页内容带偏）里就算有 HTML 也只会原样显示，不会被执行。
 *
 * 支持：代码块（```）、行内代码、标题、粗体、斜体、列表、引用、链接（只显示文字和地址，不能点）
 */

const ESC: Record<string, string> = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }

export function escapeHtml(text: string): string {
  return text.replace(/[&<>"']/g, (c) => ESC[c])
}

function inline(text: string): string {
  // 行内代码先拿出来，里面的符号不再处理
  const codes: string[] = []
  let out = escapeHtml(text).replace(/`([^`\n]+)`/g, (_, code: string) => {
    codes.push(code)
    return `\u0000${codes.length - 1}\u0000`
  })
  out = out
    .replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[^*\w])\*([^*\n]+)\*(?!\w)/g, '$1<em>$2</em>')
    .replace(/\[([^\]\n]+)\]\(([^)\s]+)\)/g, '<span class="md-link" title="$2">$1</span>')
  return out.replace(/\u0000(\d+)\u0000/g, (_, i: string) => `<code>${codes[Number(i)]}</code>`)
}

export function renderMarkdown(src: string): string {
  const out: string[] = []
  const lines = src.replace(/\r\n/g, '\n').split('\n')
  let para: string[] = []
  let list: { tag: 'ul' | 'ol'; items: string[] } | null = null
  let quote: string[] = []

  const flushPara = (): void => {
    if (para.length) out.push(`<p>${para.map(inline).join('<br>')}</p>`)
    para = []
  }
  const flushList = (): void => {
    if (list) out.push(`<${list.tag}>${list.items.map((i) => `<li>${inline(i)}</li>`).join('')}</${list.tag}>`)
    list = null
  }
  const flushQuote = (): void => {
    if (quote.length) out.push(`<blockquote>${quote.map(inline).join('<br>')}</blockquote>`)
    quote = []
  }
  const flushAll = (): void => {
    flushPara()
    flushList()
    flushQuote()
  }

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]
    const fence = line.match(/^\s*(```|~~~)\s*([\w+-]*)/)
    if (fence) {
      flushAll()
      const body: string[] = []
      i++
      while (i < lines.length && !lines[i].trimStart().startsWith(fence[1])) body.push(lines[i++])
      const lang = fence[2] ? ` data-lang="${escapeHtml(fence[2])}"` : ''
      out.push(`<pre${lang}><code>${escapeHtml(body.join('\n'))}</code></pre>`)
      continue
    }
    if (!line.trim()) {
      flushAll()
      continue
    }
    const heading = line.match(/^(#{1,6})\s+(.*)$/)
    if (heading) {
      flushAll()
      out.push(`<h4>${inline(heading[2])}</h4>`)
      continue
    }
    const bullet = line.match(/^\s*[-*+]\s+(.*)$/)
    const numbered = line.match(/^\s*\d+[.)]\s+(.*)$/)
    if (bullet || numbered) {
      flushPara()
      flushQuote()
      const tag = bullet ? 'ul' : 'ol'
      if (list && list.tag !== tag) flushList()
      list ??= { tag, items: [] }
      list.items.push((bullet ?? numbered)![1])
      continue
    }
    const q = line.match(/^>\s?(.*)$/)
    if (q) {
      flushPara()
      flushList()
      quote.push(q[1])
      continue
    }
    flushList()
    flushQuote()
    para.push(line)
  }
  flushAll()
  return out.join('')
}
