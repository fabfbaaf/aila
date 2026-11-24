/**
 * 页面里用的 t()：读的是一个响应式的语言，设置里换了语言，所有用到 t() 的地方马上重画。
 * 词典和规则在 shared/i18n.ts（中文原文就是键）。
 */
import { ref } from 'vue'
import { translate, type Lang, type Params } from '../../shared/i18n'

/** 先当中文（测试里也是）；真正用哪种在 initLang 里定，页面画出来之前 */
export const lang = ref<Lang>('zh')

export function t(zh: string, params?: Params): string {
  return translate(lang.value, zh, params)
}

const CJK = /[一-鿿]/

/**
 * 常量表（列名、动词、状态名……）包一层：读的时候才翻，读到的字符串都是现在的语言。
 * 表的结构、键都不变，代码照常 TABLE[key]、list.map(...)；只翻带中文的字符串值，别的原样
 */
export function localized<T extends object>(table: T): T {
  return new Proxy(table, {
    get(target, key, receiver) {
      const v = Reflect.get(target, key, receiver)
      if (typeof v === 'string') return CJK.test(v) ? t(v) : v
      if (v && typeof v === 'object') return localized(v as object)
      return v
    }
  })
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const pad = (n: number): string => String(n).padStart(2, '0')

/** 日期：中文「11 月 23 日 09:30」，英文「Nov 23 09:30」；time=false 不带时间 */
export function dateText(d: Date, time = false): string {
  const hm = time ? ` ${pad(d.getHours())}:${pad(d.getMinutes())}` : ''
  return lang.value === 'en' ? `${MONTHS[d.getMonth()]} ${d.getDate()}${hm}` : `${d.getMonth() + 1} 月 ${d.getDate()} 日${hm}`
}

/** 列表连接：中文用顿号，英文用逗号 */
export function joinList(items: string[]): string {
  return items.join(lang.value === 'en' ? ', ' : '、')
}

function apply(l: Lang): void {
  lang.value = l
  document.documentElement.lang = l === 'en' ? 'en' : 'zh-CN'
}

/** 启动时取一次设置里的语言，以后跟着主进程的通知换（浏览器里调试时跟着浏览器语言） */
export async function initLang(): Promise<void> {
  // 用到时才加载桥（单元测试在 Node 里跑，没有 window）
  const { bridge } = await import('./platform/bridge')
  if (!bridge) {
    apply(/^zh\b/i.test(navigator.language) ? 'zh' : 'en')
    return
  }
  apply((await bridge.languageGet()).lang)
  bridge.onLanguage((l) => apply(l))
}
