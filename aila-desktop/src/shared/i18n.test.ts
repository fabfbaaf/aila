import { readFileSync, writeFileSync } from 'fs'
import { describe, expect, it } from 'vitest'
import { isLangPref, resolveLang, translate } from './i18n'
import { TABLES, TABLE_FILES, TRANSLATED_FILES, calledKeys, literalKeys, tableKeys } from './i18nKeys'
import { EN } from './locales/en'

describe('界面语言', () => {
  it('跟着系统：系统是中文就中文，别的都英文；手动选的优先', () => {
    expect(resolveLang('auto', 'zh-CN')).toBe('zh')
    expect(resolveLang(undefined, 'zh-TW')).toBe('zh')
    expect(resolveLang('auto', 'en-US')).toBe('en')
    expect(resolveLang('auto', 'ja')).toBe('en')
    expect(resolveLang('zh', 'en-US')).toBe('zh')
    expect(isLangPref('en')).toBe(true)
    expect(isLangPref('fr')).toBe(false)
  })

  it('中文原文就是键；没有英文就原样；{名字} 填进去', () => {
    expect(translate('zh', '她记得的事')).toBe('她记得的事')
    expect(translate('en', '她记得的事')).toBe(EN['她记得的事'])
    expect(translate('en', '词典里没有的一句')).toBe('词典里没有的一句')
    expect(translate('zh', '找到 {n} 条', { n: 3 })).toBe('找到 3 条')
    expect(translate('en', '找到 {n} 条', { n: 3 })).toBe(EN['找到 {n} 条'].replace('{n}', '3'))
  })

  it('已经换成英文界面的地方，每句都有英文，英文里的 {名字} 和中文对得上', () => {
    const keys = new Set<string>()
    for (const f of TRANSLATED_FILES) for (const k of calledKeys(readFileSync(f, 'utf8'))) keys.add(k)
    for (const f of TABLE_FILES) for (const k of literalKeys(readFileSync(f, 'utf8'))) keys.add(k)
    for (const [f, names] of Object.entries(TABLES)) {
      for (const n of names) for (const k of tableKeys(readFileSync(f, 'utf8'), n)) keys.add(k)
    }
    const missing = [...keys].filter((k) => !(k in EN))
    if (process.env.I18N_DUMP) writeFileSync(process.env.I18N_DUMP, JSON.stringify(missing, null, 1))
    expect(missing).toEqual([])
    const holes = (s: string): string[] => [...s.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort()
    const mismatched = [...keys].filter((k) => holes(k).join() !== holes(EN[k]).join())
    expect(mismatched).toEqual([])
    // 英文里不该还留着中文
    expect(Object.entries(EN).filter(([, v]) => /[一-鿿]/.test(v)).map(([k]) => k)).toEqual([])
  })
})
