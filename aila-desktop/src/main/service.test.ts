/** 假进程和假时钟验证托管状态机；真实临时端口只用于检查 HTTP 与端口冲突分类。 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createServer as httpServer } from 'node:http'
import { createServer as tcpServer, type Server } from 'node:net'
import { join, resolve } from 'node:path'
import { ServiceKeeper, type Health, type ServiceCommand, type ServiceDependencies, type ServiceOutput, type ServiceProcess } from './service'
import { checkServiceHealth, locateService, packagedCommand } from './serviceNode'
import { registerServiceIpc } from './serviceIpc'

const COMMAND = { executable: 'python', args: ['-m', 'app'], cwd: '/aila-ai' }

function fixture() {
  let health: Health = 'offline'
  let command: ServiceCommand | null = COMMAND
  const children: Array<{ process: ServiceProcess; output: ServiceOutput; exit: (reason: string) => void }> = []
  const deps: ServiceDependencies = {
    command: () => command,
    health: vi.fn(async () => health),
    spawn: vi.fn((_command, output, exit) => {
      const process = { pid: children.length + 100 }
      children.push({ process, output, exit })
      return process
    }),
    stop: vi.fn(async () => {}),
    now: Date.now,
    later: (callback, delay) => { const timer = setTimeout(callback, delay); return () => clearTimeout(timer) },
    changed: vi.fn(),
    port: '8765'
  }
  return { keeper: new ServiceKeeper(deps), deps, children, health: (value: Health) => { health = value }, missing: () => { command = null } }
}

beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(1_000_000) })
afterEach(() => vi.useRealTimers())
const advance = (delay: number) => vi.advanceTimersByTimeAsync(delay)

describe('AI 服务托管状态机', () => {
  it('外部服务不启动、不重启、不结束', async () => {
    const f = fixture()
    f.health('ready')
    await f.keeper.start()
    expect(f.keeper.status()).toEqual({ phase: 'external', pid: null, error: '' })
    expect(await f.keeper.restart()).toMatchObject({ ok: false, error: expect.stringContaining('别人') })
    await f.keeper.shutdown()
    expect(f.deps.spawn).not.toHaveBeenCalled()
    expect(f.deps.stop).not.toHaveBeenCalled()
  })

  it('正常启动，半秒检查直到准备好；重复 start 不开第二个进程', async () => {
    const f = fixture()
    await f.keeper.start()
    await f.keeper.start()
    expect(f.keeper.status()).toMatchObject({ phase: 'starting', pid: 100 })
    expect(f.deps.spawn).toHaveBeenCalledWith(COMMAND, expect.any(Function), expect.any(Function))
    await advance(500)
    expect(f.keeper.status().phase).toBe('starting')
    f.health('ready')
    await advance(500)
    expect(f.keeper.status().phase).toBe('ready')
    expect(f.children).toHaveLength(1)
    await f.keeper.shutdown()
  })

  it('崩溃按 1、3、9 秒重试，第四次暂停；手动重启清空次数', async () => {
    const f = fixture()
    await f.keeper.start()
    for (const [i, delay] of [1000, 3000, 9000].entries()) {
      f.children[i].exit('退出码 1')
      await advance(0)
      expect(f.keeper.status().phase).toBe('crashed')
      await advance(delay - 1)
      expect(f.children).toHaveLength(i + 1)
      await advance(1)
      expect(f.children).toHaveLength(i + 2)
    }
    f.children[3].exit('又停了')
    await advance(0)
    expect(f.keeper.status().phase).toBe('failed')
    await advance(60_000)
    expect(f.children).toHaveLength(4)
    expect(await f.keeper.restart()).toEqual({ ok: true })
    expect(f.children).toHaveLength(5)
    f.children[4].exit('退出')
    await advance(1000)
    expect(f.children).toHaveLength(6)
    await f.keeper.shutdown()
  })

  it('五分钟前的自动重启不占次数', async () => {
    const f = fixture()
    await f.keeper.start()
    f.children[0].exit('退出')
    await advance(1000)
    f.health('ready')
    await advance(300_000)
    f.children[1].exit('退出')
    await advance(1000)
    expect(f.children).toHaveLength(3)
    await f.keeper.shutdown()
  })

  it('60 秒启动超时先关旧进程再重试', async () => {
    const f = fixture()
    await f.keeper.start()
    await advance(59_999)
    expect(f.deps.stop).not.toHaveBeenCalled()
    await advance(1)
    expect(f.keeper.status()).toMatchObject({ phase: 'crashed', error: expect.stringContaining('60 秒') })
    expect(f.deps.stop).toHaveBeenCalledWith(f.children[0].process)
    await advance(1000)
    expect(f.children).toHaveLength(2)
    await f.keeper.shutdown()
  })

  it('ready 每十秒检查，连续三次失败才重启，恢复会重置连续次数', async () => {
    const f = fixture()
    await f.keeper.start()
    f.health('ready')
    await advance(500)
    f.health('offline')
    await advance(20_000)
    expect(f.keeper.status().phase).toBe('ready')
    f.health('ready')
    await advance(10_000)
    f.health('offline')
    await advance(20_000)
    expect(f.keeper.status().phase).toBe('ready')
    await advance(10_000)
    expect(f.keeper.status().phase).toBe('crashed')
    expect(f.deps.stop).toHaveBeenCalledWith(f.children[0].process)
    await f.keeper.shutdown()
  })

  it('端口冲突直接 failed，不启动、不重试；启动后发现冲突也只关自有进程', async () => {
    const f = fixture()
    f.health('occupied')
    await f.keeper.start()
    expect(f.keeper.status()).toMatchObject({ phase: 'failed', error: expect.stringContaining('8765 端口') })
    expect(f.deps.spawn).not.toHaveBeenCalled()
    f.health('offline')
    await f.keeper.restart()
    f.health('occupied')
    await advance(500)
    expect(f.keeper.status().phase).toBe('failed')
    expect(f.deps.stop).toHaveBeenCalledWith(f.children[0].process)
    await f.keeper.shutdown()
  })

  it('缺少 Python/服务目录给建虚拟环境的说明', async () => {
    const f = fixture()
    f.missing()
    await f.keeper.start()
    expect(f.keeper.status()).toMatchObject({ phase: 'missing', error: expect.stringContaining('py -3.12 -m venv') })
    expect(f.deps.spawn).not.toHaveBeenCalled()
    await f.keeper.shutdown()
  })

  it('保留最后 500 行，合并数据块，按行带时间；不输出密钥，超长行也有界', async () => {
    const f = fixture()
    await f.keeper.start()
    const { output } = f.children[0]
    output('合并')
    output('后的行\r')
    output('\n')
    expect(f.keeper.logs()[0].text).toBe('合并后的行')
    output(Array.from({ length: 600 }, (_, i) => `行 ${i}\n`).join(''))
    expect(f.keeper.logs()).toHaveLength(500)
    expect(f.keeper.logs()[0]).toEqual({ time: new Date(Date.now()).toISOString(), text: '行 100' })
    output('\x1b[31mAPI_KEY=super-secret\x1b[0m\nBearer hidden\nsk-abcdef\n')
    output('{"LLM_API_KEY":"json-secret"}\n')
    expect(JSON.stringify(f.keeper.logs())).not.toMatch(/super-secret|Bearer hidden|sk-abcdef|json-secret|\u001b/)
    output('a'.repeat(100_000))
    expect(f.keeper.logs().every((line) => line.text.length <= 4000)).toBe(true)
    await f.keeper.shutdown()
    expect(f.keeper.logs()).toHaveLength(500)
  })

  it('退出只关自有进程，清理重试和旧回调，不再重启', async () => {
    const f = fixture()
    await f.keeper.start()
    const old = f.children[0]
    await f.keeper.shutdown()
    old.exit('迟到的退出事件')
    old.output('迟到的日志\n')
    await advance(90_000)
    expect(f.deps.stop).toHaveBeenCalledTimes(1)
    expect(f.deps.stop).toHaveBeenCalledWith(old.process)
    expect(f.children).toHaveLength(1)
    expect(f.keeper.logs()).toEqual([])
  })

  it('stdout 和 stderr 交错时各自拼行，脱敏仍按完整行处理', async () => {
    const f = fixture()
    await f.keeper.start()
    const { output } = f.children[0]
    output('API_', 'stdout')
    output('出错信息\n', 'stderr')
    output('KEY=hidden-value\n', 'stdout')
    expect(f.keeper.logs().map(({ text }) => text)).toEqual(['出错信息', 'API_KEY=[已隐藏]'])
    await f.keeper.shutdown()
  })

  it('停止最多等三秒，退出中未完成的手动重启不能再启动服务', async () => {
    const f = fixture()
    await f.keeper.start()
    f.deps.stop = vi.fn(() => new Promise<void>(() => {}))
    const restart = f.keeper.restart()
    const shutdown = f.keeper.shutdown()
    await advance(3000)
    await shutdown
    expect(await restart).toMatchObject({ ok: false })
    expect(f.keeper.status().phase).toBe('stopped')
    expect(f.children).toHaveLength(1)
  })

  it('健康请求卡住也在 60 秒截止，退出会等正在进行的进程清理', async () => {
    const f = fixture()
    await f.keeper.start()
    f.deps.health = () => new Promise<Health>(() => {})
    f.deps.stop = () => new Promise<void>(() => {})
    await advance(60_000)
    expect(f.keeper.status().phase).toBe('crashed')
    let stopped = false
    const stopping = f.keeper.shutdown().then(() => { stopped = true })
    await advance(2999)
    expect(stopped).toBe(false)
    await advance(1)
    await stopping
    expect(f.keeper.status().phase).toBe('stopped')
    expect(f.children).toHaveLength(1)
  })

  it('spawn 抛错按失败重试，不留下假的进程号', async () => {
    const f = fixture()
    f.deps.spawn = () => { throw new Error('启动失败') }
    await f.keeper.start()
    expect(f.keeper.status()).toMatchObject({ phase: 'crashed', pid: null })
    await advance(13_000)
    expect(f.keeper.status().phase).toBe('failed')
    await f.keeper.shutdown()
  })
})

describe('服务定位与 IPC 边界', () => {
  it('开发使用仓库虚拟环境，环境变量优先，打包入口留给安装包任务', () => {
    const location = { packaged: false, appPath: resolve('test/repo/aila-desktop'), env: {}, platform: 'win32' as const, file: () => true, directory: () => true }
    expect(locateService(location)?.executable).toBe(join(location.appPath, '..', 'aila-ai', '.venv', 'Scripts', 'python.exe'))
    expect(locateService({ ...location, platform: 'linux' })?.executable).toBe(join(location.appPath, '..', 'aila-ai', '.venv', 'bin', 'python'))
    expect(locateService({ ...location, env: { AILA_AI_PYTHON: 'custom/python.exe', AILA_AI_DIR: 'custom/service' } })).toMatchObject({ cwd: resolve('custom/service'), executable: resolve('custom/python.exe'), args: ['-m', 'app'] })
    expect(locateService({ ...location, file: () => false })).toBeNull()
    expect(locateService({ ...location, directory: () => false })).toBeNull()
    expect(locateService({ ...location, packaged: true })).toBeNull()
    expect(packagedCommand()).toBeNull()
  })

  it('状态谁都可读；其他窗口不能取日志或重启', async () => {
    const f = fixture()
    const handlers = new Map<string, (...args: any[]) => any>()
    const handle = vi.fn((name, fn) => { handlers.set(name, fn) })
    const main = {}
    registerServiceIpc({ handle }, f.keeper, (event) => event.sender === main)
    expect(handlers.get('service:status')?.({ sender: {} })).toMatchObject({ phase: 'checking' })
    expect(await handlers.get('service:restart')?.({ sender: {} })).toMatchObject({ ok: false })
    expect(handlers.get('service:logs')?.({ sender: {} })).toMatchObject({ ok: false })
    expect(handlers.get('service:logs')?.({ sender: main })).toEqual({ ok: true, data: [] })
    expect(await handlers.get('service:restart')?.({ sender: main })).toEqual({ ok: true })
    await f.keeper.shutdown()
  })
})

describe('直连健康接口', () => {
  it('分辨艾拉、普通 HTTP、非 HTTP 端口和未监听端口', async () => {
    vi.useRealTimers()
    async function listen(server: Server): Promise<string> {
      await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
      const address = server.address()
      return `http://127.0.0.1:${typeof address === 'object' && address ? address.port : 0}`
    }
    const server = httpServer((_req, res) => res.end(JSON.stringify({ ok: true, model: 'mock' })))
    const url = await listen(server)
    try { expect(await checkServiceHealth(url, new AbortController().signal)).toBe('ready') }
    finally { await new Promise<void>((resolve) => server.close(() => resolve())) }
    expect(await checkServiceHealth(url, new AbortController().signal)).toBe('offline')
    const other = httpServer((_req, res) => res.end('another app'))
    const otherUrl = await listen(other)
    try { expect(await checkServiceHealth(otherUrl, new AbortController().signal)).toBe('occupied') }
    finally { await new Promise<void>((resolve) => other.close(() => resolve())) }
    const raw = tcpServer((socket) => { socket.on('error', () => {}); socket.end('not HTTP\r\n'); socket.resume() })
    const rawUrl = await listen(raw)
    try { expect(await checkServiceHealth(rawUrl, new AbortController().signal)).toBe('occupied') }
    finally { await new Promise<void>((resolve) => raw.close(() => resolve())) }
  })
})
