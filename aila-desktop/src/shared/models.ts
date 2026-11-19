/**
 * 下载语音模型的进度。安装包不带语音模型（约 460MB），设置页「语音」里点按钮下载：
 * 主进程用 AI 服务同一个程序跑 --download-models，它每行打一条（格式见 aila-ai/app/models_download.py），
 * 这里把这些行折成界面要的状态。纯函数，方便单测。
 */

export interface ModelDownloadState {
  running: boolean
  /** 正在下的文件（模型名） */
  current: string | null
  doneMb: number
  totalMb: number
  /** 最近一句给人看的话：「解压：……」「完成：……」 */
  message: string
  error: string | null
  /** 全部下完了（之后要重启 AI 服务才会加载） */
  finished: boolean
}

export const IDLE_DOWNLOAD: ModelDownloadState = {
  running: false, current: null, doneMb: 0, totalMb: 0, message: '', error: null, finished: false
}

const PROGRESS = /^进度 (\S+) (\d+) (\d+)$/

export function applyDownloadLine(state: ModelDownloadState, raw: string): ModelDownloadState {
  const line = raw.trim()
  if (!line) return state
  const m = PROGRESS.exec(line)
  if (m) return { ...state, current: m[1], doneMb: Number(m[2]), totalMb: Number(m[3]), message: '' }
  if (line === '全部完成') return { ...state, finished: true, message: '全部下载好了' }
  if (line.startsWith('下载失败：')) return { ...state, error: line.slice('下载失败：'.length) }
  if (/^(解压|完成|已存在，跳过)：|^下载向量模型/.test(line)) return { ...state, message: line }
  return state
}

/** 进度条下面那一行字 */
export function describeDownload(s: ModelDownloadState): string {
  if (s.error) return `没下完：${s.error}。网络好了再点一次，已经下好的不会重下`
  if (s.finished) return s.running ? '下载好了，正在重启 AI 服务……' : '下载好了，语音可以用了'
  if (s.message) return s.message
  if (s.current) return s.totalMb ? `${s.current}：${s.doneMb} / ${s.totalMb} MB` : `${s.current}：${s.doneMb} MB`
  return s.running ? '准备下载……' : ''
}
