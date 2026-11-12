import { describe, expect, it } from 'vitest'
import { sentenceCut, speakableLength, splitSentences } from './sentences'

describe('splitSentences', () => {
  it('第一句遇到逗号就切，后面的到句号再切', () => {
    expect(splitSentences('今天北京晴，最高气温十五度，适合出门。明天有雨，记得带伞。')).toEqual([
      '今天北京晴，',
      '最高气温十五度，适合出门。',
      '明天有雨，记得带伞。'
    ])
  })

  it('第一句太短时不在逗号处切', () => {
    expect(splitSentences('好的，马上开始整理。')).toEqual(['好的，马上开始整理。'])
  })

  it('开头的省略号和后面的话算一句', () => {
    expect(splitSentences('……我在。有什么需要吗？')).toEqual(['……我在。', '有什么需要吗？'])
  })

  it('句中省略号后面开始新内容时切开', () => {
    expect(splitSentences('诶……出错了吗？')).toEqual(['诶……', '出错了吗？'])
  })

  it('小数点和版本号不切', () => {
    expect(splitSentences('Vue 3.5 和 Python 3.13 都发布了。')).toEqual(['Vue 3.5 和 Python 3.13 都发布了。'])
  })

  it('很长的句子在逗号处切，超长时硬切', () => {
    const long = '这是一个非常非常长的句子，'.repeat(3) + '结束。'
    const parts = splitSentences('开头。' + long)
    expect(parts.length).toBeGreaterThan(2)
    expect(parts.join('')).toBe('开头。' + long)
    expect(splitSentences('啊'.repeat(200)).every((p) => p.length <= 80)).toBe(true)
  })

  it('只有标点的不单独成句', () => {
    expect(splitSentences('……')).toEqual(['……'])
    expect(sentenceCut('', '。', false)).toBe('none')
  })
})

describe('speakableLength', () => {
  it('只数汉字、字母和数字', () => {
    expect(speakableLength('……Vue 3，好！')).toBe(5)
  })
})
