/** 托管服务的 Node 适配器：定位可执行文件、直连本地健康接口、仅结束自己创建的进程树。 */
import { spawn, execFile, type ChildProcess } from 'node:child_process'
import { mkdirSync, readFileSync, statSync } from 'node:fs'
import { request } from 'node:http'
import { request as secureRequest } from 'node:https'
import { createConnection } from 'node:net'
import { resolve, join } from 'node:path'
import { StringDecoder } from 'node:string_decoder'
import { ServiceKeeper, type Health, type ServiceCommand, type ServiceProcess } from './service'
import type { ServiceState } from '../shared/service'
import { tm } from './lang'

export interface ServiceLocation {
  packaged: boolean
  appPath: string
  env: NodeJS.ProcessEnv
  platform: NodeJS.Platform
  file: (path: string) => boolean
  directory: (path: string) => boolean
  /** 本机测试版（npm run pack:local）打包时记下的仓库 aila-ai 目录；正式安装包没有这个 */
  localDir?: string
  /** 安装包的 resources 目录（里面有 aila-ai/aila-ai.exe） */
  resourcesPath?: string
  /** app.getPath('userData')：用户的密钥、会话、模型都放这里，不写安装目录 */
  userData?: string
}

/**
 * 安装包：resources/aila-ai/aila-ai.exe（scripts/build_service.py 打的，里面带了 Python）。
 * 用户的东西全部指到 userData/ai/ 下面：卸载、升级都不会动它们，安装目录里也不会多出文件。
 * .env 第一次在设置页保存时由 AI 服务照着安装包里的 .env.example 建；没有时就是没填密钥（用假模型）。
 * 找不到 exe 时返回 null（状态是「找不到 AI 服务」），不去猜一个系统 Python。
 */
export function packagedCommand(location: Pick<ServiceLocation, 'resourcesPath' | 'userData' | 'file'>): ServiceCommand | null {
  const { resourcesPath, userData } = location
  if (!resourcesPath || !userData) return null
  const executable = join(resourcesPath, 'aila-ai', 'aila-ai.exe')
  if (!location.file(executable)) return null
  const home = join(userData, 'ai')
  return {
    executable,
    args: [],
    cwd: home,
    env: {
      AILA_ENV_FILE: join(home, '.env'),
      AILA_DATA_DIR: join(home, 'data'),
      AILA_MODELS_DIR: join(home, 'models'),
      // 安装包里带的模型（语音合成 melo）：没下载过也能说话
      AILA_BUNDLED_MODELS_DIR: join(resourcesPath, 'models'),
      AILA_MCP_CONFIG: join(home, 'mcp.json')
    }
  }
}

/** 本机测试版的记录：resources/local-service.json 里的 {"aiDir": "..."}；没有、读不了、格式不对都当没有 */
export function readLocalService(resourcesPath: string, read = (path: string) => readFileSync(path, 'utf8')): string | undefined {
  try {
    const data = JSON.parse(read(join(resourcesPath, 'local-service.json'))) as { aiDir?: unknown }
    return typeof data.aiDir === 'string' && data.aiDir ? data.aiDir : undefined
  } catch {
    return undefined
  }
}

export function locateService(location: ServiceLocation): ServiceCommand | null {
  const { env, platform } = location
  const explicit = env['AILA_AI_PYTHON'] || env['AILA_AI_DIR']
  if (location.packaged && !explicit && !location.localDir) return packagedCommand(location)
  // 本机测试版：用打包时记下的仓库目录（还是这台电脑上的虚拟环境）
  const cwd = resolve(env['AILA_AI_DIR'] || (location.packaged && location.localDir) || join(location.appPath, '..', 'aila-ai'))
  const executable = resolve(env['AILA_AI_PYTHON'] || join(cwd, '.venv', platform === 'win32' ? 'Scripts/python.exe' : 'bin/python'))
  return location.directory(cwd) && location.file(executable) ? { executable, args: ['-m', 'app'], cwd } : null
}

function hasType(path: string, directory: boolean): boolean {
  try { return directory ? statSync(path).isDirectory() : statSync(path).isFile() }
  catch { return false }
}

function portOpen(url: URL, signal: AbortSignal): Promise<boolean> {
  return new Promise((resolve) => {
    const socket = createConnection({ host: url.hostname, port: Number(url.port || (url.protocol === 'https:' ? 443 : 80)) })
    const finish = (open: boolean): void => { signal.removeEventListener('abort', abort); socket.destroy(); resolve(open) }
    const abort = (): void => finish(false)
    socket.once('connect', () => finish(true))
    socket.once('error', () => finish(false))
    socket.setTimeout(500, () => finish(false))
    signal.addEventListener('abort', abort, { once: true })
    if (signal.aborted) abort()
  })
}

/** 不经过代理：HTTP 响应无艾拉格式，或 TCP 能连上却不说 HTTP，都属于端口冲突。 */
export async function checkServiceHealth(baseUrl: string, signal: AbortSignal): Promise<Health> {
  const url = new URL(baseUrl.replace(/\/$/, '') + '/health')
  const result = await new Promise<Health | null>((resolve) => {
    const send = url.protocol === 'https:' ? secureRequest : request
    // 4 秒：她在忙（加载模型、建索引）时健康检查会慢一点，不算没回应
    const req = send(url, { signal, timeout: 4000, agent: false }, (res) => {
      let body = ''
      res.setEncoding('utf8')
      res.on('data', (part: string) => {
        body += part
        if (body.length > 4096) { resolve('occupied'); req.destroy() }
      })
      res.once('end', () => {
        try {
          const data: unknown = JSON.parse(body)
          if (!data || typeof data !== 'object' || typeof (data as { ok?: unknown }).ok !== 'boolean' || typeof (data as { model?: unknown }).model !== 'string') resolve('occupied')
          else resolve(res.statusCode === 200 && (data as { ok: boolean }).ok ? 'ready' : 'offline')
        } catch { resolve('occupied') }
      })
      res.once('error', () => resolve(null))
    })
    req.once('timeout', () => { req.destroy(); resolve(null) })
    req.once('error', () => resolve(null))
    req.end()
  })
  if (signal.aborted) return 'offline'
  return result ?? (await portOpen(url, signal) ? 'occupied' : 'offline')
}

interface OwnedProcess extends ServiceProcess { child: ChildProcess }

/** 找 AI 服务的启动方式（每次调用都重新找：开发时可能刚建好虚拟环境）。下载语音模型也用它 */
export function serviceLocator(options: { packaged: boolean; appPath: string; userData: string }): () => ServiceCommand | null {
  const localDir = options.packaged ? readLocalService(process.resourcesPath) : undefined
  const resourcesPath = options.packaged ? process.resourcesPath : undefined
  return () => locateService({ ...options, localDir, resourcesPath, env: process.env, platform: process.platform, file: (path) => hasType(path, false), directory: (path) => hasType(path, true) })
}

export function createServiceKeeper(options: { locate: () => ServiceCommand | null; url: string; changed: (state: ServiceState) => void }): ServiceKeeper {
  const endpoint = new URL(options.url)
  return new ServiceKeeper({
    port: endpoint.port || (endpoint.protocol === 'https:' ? '443' : '80'),
    command: options.locate,
    now: Date.now,
    later: (callback, delay) => { const timer = setTimeout(callback, delay); return () => clearTimeout(timer) },
    health: (signal) => checkServiceHealth(options.url, signal),
    changed: options.changed,
    spawn: (command, output, exit) => {
      // 安装包第一次运行时 userData/ai 还不存在：工作目录不存在 spawn 会直接失败
      mkdirSync(command.cwd, { recursive: true })
      const child = spawn(command.executable, command.args, {
        cwd: command.cwd,
        env: { ...process.env, ...command.env, AILA_HOST: endpoint.hostname, AILA_PORT: endpoint.port || '8765', PYTHONUNBUFFERED: '1', PYTHONIOENCODING: 'utf-8' },
        windowsHide: true,
        detached: process.platform !== 'win32',
        stdio: ['ignore', 'pipe', 'pipe']
      })
      // 只记录路径和参数，永远不枚举环境变量；stdout/stderr 的 UTF-8 汉字可能跨数据块。
      queueMicrotask(() => output(`启动 ${command.executable} ${command.args.join(' ')}\n`))
      for (const source of ['stdout', 'stderr'] as const) {
        const stream = child[source]
        const decoder = new StringDecoder('utf8')
        stream?.on('data', (chunk: Buffer) => output(decoder.write(chunk), source))
        stream?.on('end', () => output(decoder.end(), source))
      }
      child.once('error', () => exit(tm('AI 服务没能启动。检查 Python 路径和目录是否可访问。')))
      child.once('close', (code, signal) => exit(tm('AI 服务退出了（{why}）。', { why: code === null ? tm('信号 {s}', { s: signal ?? tm('未知') }) : tm('退出码 {c}', { c: code }) })))
      if (!child.pid) throw new Error('AI 服务没有进程号')
      return { pid: child.pid, child } as OwnedProcess
    },
    stop: async (process_) => {
      const { child, pid } = process_ as OwnedProcess
      if (child.exitCode !== null || child.signalCode !== null) return
      if (process.platform === 'win32') {
        await new Promise<void>((resolve) => execFile('taskkill', ['/PID', String(pid), '/T', '/F'], { windowsHide: true, timeout: 3000 }, () => resolve()))
      } else {
        try { process.kill(-pid, 'SIGTERM') } catch { return }
        await new Promise<void>((resolve) => {
          const timer = setTimeout(() => { try { process.kill(-pid, 'SIGKILL') } catch { /* 已经退出 */ }; resolve() }, 2800)
          child.once('exit', () => { clearTimeout(timer); resolve() })
        })
      }
    }
  })
}
