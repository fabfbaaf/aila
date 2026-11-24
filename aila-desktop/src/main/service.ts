/** 看管自己启动的 AI 服务。进程、健康检查、时钟由外部提供，状态机可以离线测试。
 * 每次开始/停止都换一代：旧进程回调、旧健康请求和旧计时器不能重启已经退出的应用。
 */
import type { ServiceLog, ServiceResult, ServiceState } from '../shared/service'
import { tm } from './lang'

export interface ServiceCommand {
  executable: string
  args: string[]
  cwd: string
  /** 额外的环境变量（安装包里用来把用户的文件指到 userData） */
  env?: Record<string, string>
}

export type Health = 'ready' | 'offline' | 'occupied'

export interface ServiceProcess {
  pid: number
}

export type ServiceOutput = (text: string, source?: 'stdout' | 'stderr') => void

export interface ServiceDependencies {
  command: () => ServiceCommand | null
  health: (signal: AbortSignal) => Promise<Health>
  spawn: (command: ServiceCommand, output: ServiceOutput, exit: (reason: string) => void) => ServiceProcess
  stop: (process: ServiceProcess) => Promise<void>
  now: () => number
  later: (callback: () => void, milliseconds: number) => () => void
  changed: (state: ServiceState) => void
  port: string
}

/**
 * 起来以后连续几次（每次隔 10 秒）没回应才当它挂了重启。以前是 3 次：刚启动那几分钟她要下载向量模型、
 * 建代码索引、加载语音，一忙起来健康检查就慢，被当成挂了重启，重启后又从头忙，反复重启。
 * 进程真退出了不靠这个（exit 事件马上就知道），所以放宽到 6 次（约一分钟）只影响「卡住」的判断。
 */
const MAX_MISSES = 6
const MISSING ='找不到 AI 服务或 Python。在 aila-ai 里运行 py -3.12 -m venv .venv，再用 .venv\\Scripts\\pip install -r requirements.txt 安装依赖。也可以设置 AILA_AI_PYTHON 和 AILA_AI_DIR。'

/** 日志只记输出；常见密钥和 URL 里的认证信息在进入缓冲前就去掉。 */
export function cleanServiceLog(text: string): string {
  return text.replace(/\x1b\[[0-9;?]*[ -/]*[@-~]/g, '')
    .replace(/((?:[A-Z_]*(?:API_KEY|TOKEN|PASSWORD|SECRET)|api[_-]?key|authorization)["']?\s*[=:]\s*["']?)[^\s"',}]+/gi, tm('$1[已隐藏]'))
    .replace(/\bBearer\s+\S+/gi, tm('Bearer [已隐藏]'))
    .replace(/\bsk-[\w-]+/g, tm('[已隐藏]'))
    .replace(/(https?:\/\/)[^\s/@]+:[^\s/@]+@/g, tm('$1[已隐藏]@'))
}

export class ServiceKeeper {
  private state: ServiceState = { phase: 'checking', pid: null, error: '' }
  private process: ServiceProcess | null = null
  private stopping = new Set<Promise<void>>()
  private generation = 0
  private active = false
  private cancelTimer: (() => void) | null = null
  private cancelStartup: (() => void) | null = null
  private request: AbortController | null = null
  private retries: number[] = []
  private lines: ServiceLog[] = []
  private partial = { stdout: '', stderr: '' }
  private startupAt = 0
  private missed = 0
  private restarting: Promise<ServiceResult> | null = null

  constructor(private readonly deps: ServiceDependencies) {}

  status(): ServiceState { return { ...this.state } }
  logs(): ServiceLog[] { return this.lines.map((line) => ({ ...line })) }

  async start(): Promise<void> {
    if (this.active) return
    this.active = true
    const generation = ++this.generation
    this.set('checking')
    const health = await this.check()
    if (!this.current(generation)) return
    if (health === 'ready') this.set('external')
    else if (health === 'occupied') this.occupied()
    else this.launch(generation)
  }

  restart(): Promise<ServiceResult> {
    if (this.state.phase === 'external') return Promise.resolve({ ok: false, error: tm('这个服务是别人开的，艾拉不能替它重启。先去原来的终端停掉它。') })
    if (this.restarting) return this.restarting
    this.restarting = this.restartOwned().finally(() => { this.restarting = null })
    return this.restarting
  }

  private async restartOwned(): Promise<ServiceResult> {
    const stopped = this.shutdown()
    const generation = this.generation
    await stopped
    if (this.generation !== generation) return { ok: false, error: tm('艾拉正在退出，服务没有重启。') }
    this.retries = []
    await this.start()
    return this.state.phase === 'failed' || this.state.phase === 'missing'
      ? { ok: false, error: this.state.error } : { ok: true }
  }

  async shutdown(): Promise<void> {
    this.active = false
    const generation = ++this.generation
    this.clearWait()
    const process = this.process
    this.process = null
    const pending = [...this.stopping]
    if (process) pending.push(this.stop(process))
    await Promise.all(pending)
    this.flush()
    if (this.generation === generation) this.set('stopped')
  }

  private current(generation: number): boolean { return this.active && generation === this.generation }

  private set(phase: ServiceState['phase'], error = ''): void {
    const next = { phase, pid: this.process?.pid ?? null, error: cleanServiceLog(error) }
    if (this.state.phase === next.phase && this.state.pid === next.pid && this.state.error === next.error) return
    this.state = next
    this.deps.changed(this.status())
  }

  private clearWait(): void {
    this.cancelTimer?.()
    this.cancelTimer = null
    this.cancelStartup?.()
    this.cancelStartup = null
    this.request?.abort()
    this.request = null
  }

  private async check(): Promise<Health> {
    const request = new AbortController()
    this.request = request
    try { return await this.deps.health(request.signal) }
    catch { return 'offline' }
    finally { if (this.request === request) this.request = null }
  }

  private wait(milliseconds: number, callback: () => void): void {
    this.cancelTimer?.()
    this.cancelTimer = this.deps.later(() => { this.cancelTimer = null; callback() }, milliseconds)
  }

  private occupied(): void {
    this.set('failed', tm('{port} 端口被别的程序占了。停掉占用它的程序，或者改用其他端口，再点「重启服务」。', { port: this.deps.port }))
  }

  private launch(generation: number): void {
    if (!this.current(generation)) return
    const command = this.deps.command()
    if (!command) { this.set('missing', MISSING); return }
    this.startupAt = this.deps.now()
    this.missed = 0
    this.set('starting')
    let process: ServiceProcess | null = null
    try {
      process = this.deps.spawn(command, (text, source) => {
        if (process && this.current(generation) && this.process === process) this.output(text, source)
      }, (reason) => {
        if (process && this.current(generation) && this.process === process) void this.crash(generation, reason)
      })
      this.process = process
      this.set('starting')
      const owned = process
      this.cancelStartup = this.deps.later(() => {
        if (this.current(generation) && this.process === owned && this.state.phase === 'starting') {
          void this.crash(generation, tm('AI 服务启动超过 60 秒还没准备好。'))
        }
      }, 60_000)
      this.wait(500, () => { void this.poll(generation, owned) })
    } catch {
      void this.crash(generation, tm('AI 服务没能启动。看看 Python 和服务目录是否还能访问。'))
    }
  }

  private async poll(generation: number, process: ServiceProcess): Promise<void> {
    const health = await this.check()
    if (!this.current(generation) || this.process !== process) return
    if (health === 'occupied') {
      this.process = null
      await this.stop(process)
      if (this.current(generation)) this.occupied()
      return
    }
    if (health === 'ready') {
      this.cancelStartup?.()
      this.cancelStartup = null
      this.missed = 0
      this.set('ready')
    } else if (this.state.phase === 'starting') {
      if (this.deps.now() - this.startupAt >= 60_000) {
        await this.crash(generation, tm('AI 服务启动超过 60 秒还没准备好。'))
        return
      }
    } else if (++this.missed >= MAX_MISSES) {
      await this.crash(generation, tm('AI 服务快一分钟没有响应，正在重新启动。'))
      return
    }
    this.wait(this.state.phase === 'starting' ? 500 : 10_000, () => { void this.poll(generation, process) })
  }

  private async crash(generation: number, reason: string): Promise<void> {
    this.clearWait()
    const process = this.process
    this.process = null
    this.flush()
    this.set('crashed', reason || tm('AI 服务意外退出。'))
    if (process) await this.stop(process)
    if (!this.current(generation)) return
    const now = this.deps.now()
    this.retries = this.retries.filter((time) => now - time < 300_000)
    if (this.retries.length >= 3) {
      this.set('failed', tm('AI 服务在 5 分钟内多次停下，已经暂停自动重启。看一下日志，再点「重启服务」。'))
      return
    }
    const delay = [1000, 3000, 9000][this.retries.length]
    this.retries.push(now)
    this.wait(delay, () => this.launch(generation))
  }

  private async stop(process: ServiceProcess): Promise<void> {
    const pending = this.stopBounded(process)
    this.stopping.add(pending)
    try { await pending }
    finally { this.stopping.delete(pending) }
  }

  private async stopBounded(process: ServiceProcess): Promise<void> {
    let cancel = (): void => {}
    try {
      await Promise.race([
        Promise.resolve().then(() => this.deps.stop(process)).catch(() => {}),
        new Promise<void>((resolve) => { cancel = this.deps.later(resolve, 3000) })
      ])
    } finally { cancel() }
  }

  private output(text: string, source: 'stdout' | 'stderr' = 'stdout'): void {
    // 两路输出的数据块会交错，只在各自的流里拼接未完成的行。
    const parts = (this.partial[source] + text).split(/\r\n|\n|\r(?!$)/)
    let partial = parts.pop() ?? ''
    for (const line of parts) this.line(line)
    // 没有换行的异常输出也不能无限占内存；截成有上限的日志行。
    while (partial.length > 4000) {
      this.line(partial.slice(0, 4000))
      partial = partial.slice(4000)
    }
    this.partial[source] = partial
  }

  private line(text: string): void {
    this.lines.push({ time: new Date(this.deps.now()).toISOString(), text: cleanServiceLog(text).slice(0, 4000) })
    if (this.lines.length > 500) this.lines.splice(0, this.lines.length - 500)
  }

  private flush(): void {
    for (const source of ['stdout', 'stderr'] as const) {
      if (this.partial[source]) this.line(this.partial[source].replace(/\r$/, ''))
      this.partial[source] = ''
    }
  }
}
