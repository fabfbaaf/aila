import { describe, expect, it } from 'vitest'
import { RELEASES_PAGE, compareVersions, dueForCheck, isReleaseUrl, parseRelease } from './update'

const release = {
  tag_name: 'v0.3.0',
  html_url: 'https://github.com/fabfbaaf/aila/releases/tag/v0.3.0',
  body: '主窗口能聊天了\r\n记忆能改',
  assets: [
    { name: 'blockmap', browser_download_url: 'https://github.com/fabfbaaf/aila/releases/download/v0.3.0/x.blockmap' },
    { name: '艾拉 Setup 0.3.0.exe', browser_download_url: 'https://github.com/fabfbaaf/aila/releases/download/v0.3.0/setup.exe' }
  ]
}

describe('检查更新', () => {
  it('比版本号', () => {
    expect(compareVersions('0.10.0', '0.9.9')).toBe(1)
    expect(compareVersions('v1.0', '1.0.0')).toBe(0)
    expect(compareVersions('1.0.0-beta', '1.0.1')).toBe(-1)
  })

  it('有新版：给发布页、安装包地址和说明', () => {
    const u = parseRelease(release, '0.2.0', new Date('2026-11-23T10:00:00Z'))
    expect(u).toEqual({
      current: '0.2.0',
      latest: '0.3.0',
      newer: true,
      page: release.html_url,
      installer: 'https://github.com/fabfbaaf/aila/releases/download/v0.3.0/setup.exe',
      notes: '主窗口能聊天了\n记忆能改',
      checkedAt: '2026-11-23T10:00:00.000Z'
    })
    expect(parseRelease(release, '0.3.0').newer).toBe(false)
    expect(parseRelease({ ...release, prerelease: true }, '0.2.0').newer).toBe(false)
  })

  it('链接不是这个仓库的不要', () => {
    const evil = { ...release, html_url: 'https://evil.example/aila', assets: [{ name: 'a.exe', browser_download_url: 'https://evil.example/a.exe' }] }
    const u = parseRelease(evil, '0.2.0')
    expect(u.page).toBe(RELEASES_PAGE)
    expect(u.installer).toBe('')
    expect(parseRelease({ tag_name: '<script>' }, '0.2.0')).toMatchObject({ latest: '', newer: false })
    expect(isReleaseUrl('https://github.com/fabfbaaf/aila/releases/download/v1/a.exe')).toBe(true)
    expect(isReleaseUrl('https://github.com/someone/else')).toBe(false)
  })

  it('自动检查一天最多一次', () => {
    const now = new Date('2026-11-23T10:00:00Z')
    expect(dueForCheck(undefined, now)).toBe(true)
    expect(dueForCheck('2026-11-23T01:00:00Z', now)).toBe(false)
    expect(dueForCheck('2026-11-22T09:00:00Z', now)).toBe(true)
  })
})
