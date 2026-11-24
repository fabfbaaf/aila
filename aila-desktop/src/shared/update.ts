/**
 * 检查更新：看 GitHub 上这个仓库最新的发布版本，比现在装的新就提示你下载。
 *
 * 为什么这样设计：
 * - 没有代码签名证书，静默自动安装做不了（Windows 会拦）；提示你、一键打开下载页就够用
 * - 只认这个仓库在 github.com 上的地址：发布信息里的链接不对就不给你看（不会被引去别的网站）
 * - 只发一个不带任何你的信息的 GET；可以在设置里关掉自动检查，只在点「检查更新」时查
 */

export const RELEASES_API = 'https://api.github.com/repos/fabfbaaf/aila/releases/latest'
export const RELEASES_PAGE = 'https://github.com/fabfbaaf/aila/releases'
const SAFE_PREFIX = 'https://github.com/fabfbaaf/aila/'

export interface UpdateInfo {
  /** 现在装的版本 */
  current: string
  /** GitHub 上最新的版本（没查到是 ''） */
  latest: string
  newer: boolean
  /** 发布页（下载就在这页） */
  page: string
  /** 安装包直接下载的地址（没有就是 ''） */
  installer: string
  /** 这一版改了什么（发布说明，截短） */
  notes: string
  checkedAt: string
  error?: string
}

/** 1.2.10 > 1.2.9；前面的 v 和后面的 -beta 这类不比 */
export function compareVersions(a: string, b: string): number {
  const parts = (v: string): number[] => v.replace(/^v/i, '').split(/[-+]/)[0].split('.').map((n) => Number.parseInt(n, 10) || 0)
  const x = parts(a)
  const y = parts(b)
  for (let i = 0; i < Math.max(x.length, y.length); i++) {
    const d = (x[i] ?? 0) - (y[i] ?? 0)
    if (d !== 0) return d > 0 ? 1 : -1
  }
  return 0
}

/** GitHub API 返回的最新发布 → 给你看的信息。链接不是这个仓库的就不要 */
export function parseRelease(raw: unknown, current: string, now = new Date()): UpdateInfo {
  const r = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>
  const tag = typeof r.tag_name === 'string' ? r.tag_name.trim() : ''
  const latest = /^v?\d+(\.\d+){0,3}([-+][\w.]+)?$/.test(tag) ? tag.replace(/^v/i, '') : ''
  const page = typeof r.html_url === 'string' && r.html_url.startsWith(SAFE_PREFIX) ? r.html_url : RELEASES_PAGE
  const assets = Array.isArray(r.assets) ? (r.assets as Record<string, unknown>[]) : []
  const exe = assets.find(
    (a) => typeof a?.browser_download_url === 'string' && a.browser_download_url.startsWith(SAFE_PREFIX) && /\.exe$/i.test(String(a.name ?? ''))
  )
  const notes = typeof r.body === 'string' ? r.body.replace(/\r\n/g, '\n').trim().slice(0, 1200) : ''
  return {
    current,
    latest,
    newer: !!latest && compareVersions(latest, current) > 0 && r.draft !== true && r.prerelease !== true,
    page,
    installer: exe ? String(exe.browser_download_url) : '',
    notes,
    checkedAt: now.toISOString()
  }
}

/** 自动检查：一天最多一次 */
export function dueForCheck(lastIso: string | undefined, now = new Date()): boolean {
  if (!lastIso) return true
  const last = new Date(lastIso).getTime()
  return Number.isNaN(last) || now.getTime() - last > 24 * 3600 * 1000
}

/** 打开下载链接前再检查一遍 */
export function isReleaseUrl(url: unknown): url is string {
  return typeof url === 'string' && (url.startsWith(SAFE_PREFIX) || url === RELEASES_PAGE)
}
