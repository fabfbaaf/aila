/**
 * 边收边切句子，给语音合成用。合成是按句进行的，第一句越短，她开口越早：
 * 第一句遇到逗号就切（至少 4 个字）；第二句也短一点（逗号处、至少 10 个字）——第一句念完只有一秒多，
 * 第二句要在这之前合成好，不然中间会停一下（实测 melo 合成 20 字要 0.8~1.1 秒）；
 * 后面的句子到句号再切，太长时在逗号处切。
 */

const HARD_STOPS = '。！？!?；;\n'
const SOFT_STOPS = '，、,：:'
const ELLIPSIS = '…⋯'
const FIRST_MIN = 4
const SECOND_MIN = 10
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
export function sentenceCut(current: string, next: string, index: number | boolean): 'before' | 'after' | 'none' {
  // index：这是第几句（从 0 数）；旧的写法传 true / false 表示是不是第一句
  const n = typeof index === 'boolean' ? (index ? 0 : 2) : index
  const softMin = n === 0 ? FIRST_MIN : n === 1 ? SECOND_MIN : SOFT_MIN
  const last = current.at(-1) ?? ''
  if (ELLIPSIS.includes(last) && !ELLIPSIS.includes(next) && speakableLength(current) > 0) return 'before'

  const text = current + next
  const len = speakableLength(text)
  if (HARD_STOPS.includes(next) && len > 0) return 'after'
  if (SOFT_STOPS.includes(next) && len >= softMin) return 'after'
  if (text.length >= MAX_LEN) return 'after'
  return 'none'
}

/** 整段文字一次切完（测试和非流式场景用） */
export function splitSentences(text: string): string[] {
  const out: string[] = []
  let cur = ''
  for (const ch of text) {
    const cut = sentenceCut(cur, ch, out.length)
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
