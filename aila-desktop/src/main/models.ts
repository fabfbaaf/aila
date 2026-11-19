/**
 * 下载语音模型：用 AI 服务同一个程序跑 --download-models（安装包里是 aila-ai.exe，开发时是 python -m app），
 * 环境变量也一样，所以模型正好下到服务读的 AILA_MODELS_DIR。按行解析进度推给主窗口，下完重启 AI 服务加载模型。
 *
 * 为什么不让 AI 服务自己下：下载要好几分钟，期间服务照常用；下完正好借重启把语音引擎重新加载一遍。
 * 同一时间只跑一个下载；关掉艾拉时一起结束。
 */
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdirSync } from 'node:fs'
import { StringDecoder } from 'node:string_decoder'
import { applyDownloadLine, IDLE_DOWNLOAD, type ModelDownloadState } from '../shared/models'
import type { ServiceCommand } from './service'

export interface ModelDownloaderDeps {
  command: () => ServiceCommand | null
  changed: (state: ModelDownloadState) => void
  /** 下完以后重启 AI 服务 */
  restart: () => Promise<unknown>
}

export class ModelDownloader {
  private state: ModelDownloadState = IDLE_DOWNLOAD
  private child: ChildProcess | null = null

  constructor(private readonly deps: ModelDownloaderDeps) {}

  status(): ModelDownloadState {
    return this.state
  }

  start(): { ok: boolean; error?: string } {
    if (this.child) return { ok: true }
    const command = this.deps.command()
    if (!command) return { ok: false, error: '找不到 AI 服务，没法下载' }
    mkdirSync(command.cwd, { recursive: true })
    const child = spawn(command.executable, [...command.args, '--download-models'], {
      cwd: command.cwd,
      env: { ...process.env, ...command.env, PYTHONUNBUFFERED: '1', PYTHONIOENCODING: 'utf-8' },
      windowsHide: true,
      stdio: ['ignore', 'pipe', 'pipe']
    })
    this.child = child
    this.set({ ...IDLE_DOWNLOAD, running: true })
    for (const stream of [child.stdout, child.stderr]) {
      const decoder = new StringDecoder('utf8') // 汉字可能跨数据块
      let rest = ''
      stream?.on('data', (chunk: Buffer) => {
        const lines = (rest + decoder.write(chunk)).split(/\r?\n/)
        rest = lines.pop() ?? ''
        let next = this.state
        for (const line of lines) next = applyDownloadLine(next, line)
        if (next !== this.state) this.set(next)
      })
    }
    child.once('error', (e) => this.finish(e.message))
    child.once('exit', (code) => this.finish(code === 0 ? null : this.state.error ?? `下载程序退出了（${code}）`))
    return { ok: true }
  }

  stop(): void {
    this.child?.kill()
  }

  private async finish(error: string | null): Promise<void> {
    if (!this.child) return
    this.child = null
    if (error) {
      this.set({ ...this.state, running: false, error })
      return
    }
    this.set({ ...this.state, finished: true, running: true })
    await this.deps.restart().catch(() => undefined)
    this.set({ ...this.state, running: false })
  }

  private set(state: ModelDownloadState): void {
    this.state = state
    this.deps.changed(state)
  }
}
