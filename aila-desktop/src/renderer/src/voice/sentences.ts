/**
 * 边收边切句子，给语音合成用。合成是按句进行的，第一句越短，她开口越早：
 * 第一句遇到逗号就切（至少 4 个字），后面的句子到句号再切，太长时在逗号处切。
 */

const HARD_STOPS = '。！？!?；;\n'
const SOFT_STOPS = '，、,：:'
const ELLIPSIS = '…⋯'
const FIRST_MIN = 4
const SOFT_MIN = 24
const MAX_LEN = 80

/** 能念出声的字数（汉字、字母、数字） */
export function speakableLength(text: string): number {
  return text.match(/[\p{L}\p{N}]/gu)?.length ?? 0
}

/**
 * 当前句子是 current，又来了一个字 next：
 * - before：先把 current 当一句交出去，next 放到下一句（省略号后面开始新内容时）
 * - after：把 next 加进来后这句就结束了
 * - none：继续攒
 */
export function sentenceCut(current: string, next: string, first: boolean): 'before' | 'after' | 'none' {
  const last = current.at(-1) ?? ''
  if (ELLIPSIS.includes(last) && !ELLIPSIS.includes(next) && speakableLength(current) > 0) return 'before'

  const text = current + next
  const len = speakableLength(text)
  if (HARD_STOPS.includes(next) && len > 0) return 'after'
  if (SOFT_STOPS.includes(next) && len >= (first ? FIRST_MIN : SOFT_MIN)) return 'after'
  if (text.length >= MAX_LEN) return 'after'
  return 'none'
}

/** 整段文字一次切完（测试和非流式场景用） */
export function splitSentences(text: string): string[] {
  const out: string[] = []
  let cur = ''
  for (const ch of text) {
    const cut = sentenceCut(cur, ch, out.length === 0)
    if (cut === 'before') {
      out.push(cur)
      cur = ch
    } else {
      cur += ch
      if (cut === 'after') {
        out.push(cur)
        cur = ''
      }
    }
  }
  if (cur) out.push(cur)
  return out
}
