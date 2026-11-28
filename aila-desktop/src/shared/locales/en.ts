/**
 * 英文词典：中文原文 → 英文。用法和规则见 shared/i18n.ts。
 * 按页面分成几份，方便对照着改；同一句话在几处出现只写一次。
 */
import { EN1 } from './en1'
import { EN2 } from './en2'
import { EN3 } from './en3'
import { EN4 } from './en4'
import { EN5 } from './en5'
import { EN6 } from './en6'
import { EN7 } from './en7'
import { EN8 } from './en8'
import { EN9 } from './en9'
import { EN10 } from './en10'
import { EN11 } from './en11'
import { EN12 } from './en12'
import { EN13 } from './en13'
import { EN14 } from './en14'
import { EN15 } from './en15'

export const EN: Record<string, string> = { ...EN1, ...EN2, ...EN3, ...EN4, ...EN5, ...EN6, ...EN7, ...EN8, ...EN9, ...EN10, ...EN11, ...EN12, ...EN13, ...EN14, ...EN15 }
