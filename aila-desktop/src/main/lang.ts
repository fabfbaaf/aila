/**
 * 主进程用的界面语言：菜单、通知、对话框，以及主进程里拼给页面看的文字（日报、提醒……）都用 tm()。
 * 语言由 index.ts 在启动、设置里换语言时定下来；规则和词典在 shared/i18n.ts。
 */
import { translate, type Lang, type Params } from '../shared/i18n'

let current: Lang = 'zh'

export function setMainLang(lang: Lang): void {
  current = lang
}

export function mainLang(): Lang {
  return current
}

export function tm(zh: string, params?: Params): string {
  return translate(current, zh, params)
}
