/**
 * 她问「要我……吗？」时，用户打字或者说的话是不是在回答。
 *
 * 宁可认不出，也不能认错：
 * - 只有整句都由「好、可以、嗯、打开吧」这类词组成才算同意（「是什么网站？」不算）
 * - 短句里带「不、别、算了、取消」就算不同意（「不是这个网站」也当不同意，反正不执行）
 * - 其他话（太长、问别的事）返回 null：当成新的一句话，回复被打断，操作不执行
 */
export type ConfirmIntent = 'yes' | 'no' | null

/** 超过这么长就不当作回答，而是用户在说别的事 */
const MAX_ANSWER_CHARS = 12

// 长的写在前面，先匹配「好的」再匹配「好」
const YES_TOKENS = [
  '没问题', '麻烦你', '打开吧', '可以的', '确定', '确认', '同意', '可以', '好的', '好啊', '好吧', '好呀', '是的',
  '去吧', '开吧', '发吧', '做吧', '打开', '谢谢', '好', '行', '嗯', '对', '是', '请', '吧', '啊', '呀', '哦', '呢', '的', '了',
  'okay', 'yeah', 'sure', 'yes', 'ok', 'yep'
]

const NO = /不|别|算了|取消|停|\bno\b|\bnope\b|cancel/i

/** 去掉标点、空格和开头的称呼 */
function normalize(text: string): string {
  return text
    .toLowerCase()
    .replace(/[\s\p{P}\p{S}]/gu, '')
    .replace(/^(艾拉)+/, '')
}

export function confirmIntent(text: string): ConfirmIntent {
  const s = normalize(text)
  if (!s || s.length > MAX_ANSWER_CHARS) return null
  if (NO.test(s)) return 'no'

  let rest = s
  while (rest) {
    const token = YES_TOKENS.find((t) => rest.startsWith(t))
    if (!token) return null
    rest = rest.slice(token.length)
  }
  return 'yes'
}
