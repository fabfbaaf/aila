/**
 * 界面语言：中文（原文）和英文。
 *
 * 为什么这样设计（像 gettext）：中文原文本身就是键，t('她记得的事') → 英文词典里有就给英文，没有就原样给中文。
 * - 不用给一千多句话起名字；代码里看到的还是原来那句中文，好找好改
 * - 漏翻的只会显示中文，不会显示一个键名；测试会扫一遍已经换成 t() 的文件，看英文是不是都有（i18n.test.ts）
 * - 句子里要填的东西写成 {名字}：t('一条最多 {n} 个字', { n: 120 })
 * 主进程（菜单、通知）和页面用同一份词典；页面那边包了一层响应式（renderer/src/i18n.ts），换语言马上生效。
 */
import { EN } from './locales/en'

export type Lang = 'zh' | 'en'
/** auto：跟着系统（系统是中文就中文，别的都用英文） */
export type LangPref = 'auto' | Lang

export const LANG_PREFS: { id: LangPref; name: string }[] = [
  { id: 'auto', name: '跟随系统 / Auto' },
  { id: 'zh', name: '中文' },
  { id: 'en', name: 'English' }
]

export function isLangPref(v: unknown): v is LangPref {
  return v === 'auto' || v === 'zh' || v === 'en'
}

/** 设置 + 系统语言（app.getLocale()、navigator.language）→ 用哪种 */
export function resolveLang(pref: LangPref | undefined, systemLocale: string): Lang {
  if (pref === 'zh' || pref === 'en') return pref
  return /^zh\b/i.test(systemLocale) ? 'zh' : 'en'
}

export type Params = Record<string, string | number>

export function translate(lang: Lang, zh: string, params?: Params): string {
  const text = lang === 'en' ? (EN[zh] ?? zh) : zh
  if (!params) return text
  return text.replace(/\{(\w+)\}/g, (all, k: string) => (k in params ? String(params[k]) : all))
}
