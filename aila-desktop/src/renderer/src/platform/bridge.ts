import type { AilaApi } from '../../../preload/index'

/** Electron 提供的桌面能力；在普通浏览器里调试时为 null */
export const bridge: AilaApi | null = window.aila ?? null

export const isDesktop = bridge !== null
