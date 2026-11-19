import { describe, expect, it } from 'vitest'
import { applyDownloadLine, describeDownload, IDLE_DOWNLOAD, type ModelDownloadState } from './models'

function feed(lines: string[]): ModelDownloadState {
  return lines.reduce(applyDownloadLine, { ...IDLE_DOWNLOAD, running: true })
}

describe('下载语音模型的进度', () => {
  it('按行折成进度', () => {
    const s = feed(['已存在，跳过：silero_vad.onnx', '进度 vits-melo-tts-zh_en 5 163', '进度 vits-melo-tts-zh_en 10 163'])
    expect(s).toMatchObject({ current: 'vits-melo-tts-zh_en', doneMb: 10, totalMb: 163, message: '' })
    expect(describeDownload(s)).toBe('vits-melo-tts-zh_en：10 / 163 MB')
    expect(describeDownload(applyDownloadLine(s, '解压：vits-melo-tts-zh_en'))).toBe('解压：vits-melo-tts-zh_en')
  })

  it('下完、出错', () => {
    const done = feed(['进度 a 1 1', '完成：a', '全部完成'])
    expect(done.finished).toBe(true)
    expect(describeDownload(done)).toBe('下载好了，正在重启 AI 服务……')
    expect(describeDownload({ ...done, running: false })).toBe('下载好了，语音可以用了')

    const failed = feed(['进度 a 3 9', '下载失败：连不上 github.com'])
    expect(failed.error).toBe('连不上 github.com')
    expect(describeDownload(failed)).toContain('没下完：连不上 github.com')
  })

  it('不认识的行不改状态', () => {
    const s = feed(['进度 a 1 9'])
    expect(applyDownloadLine(s, 'Traceback (most recent call last):')).toBe(s)
    expect(applyDownloadLine(s, '   ')).toBe(s)
  })
})
