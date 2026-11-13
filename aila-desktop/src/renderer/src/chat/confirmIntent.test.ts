import { describe, expect, it } from 'vitest'
import { confirmIntent } from './confirmIntent'

describe('confirmIntent', () => {
  it.each(['好', '好的', '好的，打开吧', '嗯嗯', '可以', '行吧', '确认', '没问题', 'OK', 'ok!', '艾拉，好的', '对的', '好啊谢谢'])(
    '「%s」算同意',
    (text) => expect(confirmIntent(text)).toBe('yes')
  )

  it.each(['不要', '不用了', '别', '算了', '取消', 'no', '不是这个网站', '好不好', '别打开'])('「%s」算不同意', (text) =>
    expect(confirmIntent(text)).toBe('no')
  )

  it.each([
    '是什么网站？', // 以「是」开头的问题不能当成同意
    '好像不太对劲，你先帮我查一下天气吧', // 太长：在说别的事
    '打开之前先告诉我这是什么',
    '帮我查一下天气',
    '',
    '……'
  ])('「%s」不是回答', (text) => expect(confirmIntent(text)).toBeNull())
})
