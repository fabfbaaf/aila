import { describe, expect, it } from 'vitest'
import { loginLaunch, openLoginWindow } from './login'

describe('登录本机 Agent', () => {
  it('Windows 上为带空格和单引号的路径打开控制台', async () => {
    const command = [String.raw`C:\Program Files\Yan's Apps\claude.exe`, 'auth', 'login']
    const started: [string, string[]][] = []
    const r = await openLoginWindow(command, 'win32', (file, args) => { started.push([file, args]) })
    expect(r.ok).toBe(true)
    expect(started[0][0]).toBe('powershell.exe')
    const launch = Buffer.from(started[0][1].at(-1)!, 'base64').toString('utf16le')
    expect(launch).toContain("'-NoExit'")
    expect(launch).toContain('-WindowStyle Normal')
    const inner = launch.match(/'([\w+/=]+)' -WindowStyle/)?.[1]
    expect(Buffer.from(inner!, 'base64').toString('utf16le')).toBe(String.raw`& 'C:\Program Files\Yan''s Apps\claude.exe' 'auth' 'login'`)
  })

  it('第二个账号只给登录进程设置目录，避免从主账号带入令牌', () => {
    const command = ['C:/tools/codex.exe', 'login']
    const profile = { key: 'CODEX_HOME', path: "C:/Data/Yan's directory/codex-alt" }
    const launch = loginLaunch(command, 'win32', profile)
    if (!launch || !('args' in launch)) throw new Error('没生成登录命令')
    const outer = Buffer.from(launch.args.at(-1)!, 'base64').toString('utf16le')
    const inner = Buffer.from(outer.match(/'([\w+/=]+)' -WindowStyle/)![1], 'base64').toString('utf16le')
    expect(inner).toContain("$env:CODEX_HOME = 'C:/Data/Yan''s directory/codex-alt'")
    expect(inner).toContain("SetEnvironmentVariable('OPENAI_API_KEY', $null, 'Process')")
    expect(inner).toContain("& 'C:/tools/codex.exe' 'login'")
    expect(loginLaunch(command, 'win32', { key: 'PATH', path: profile.path })).toBeNull()
    expect(loginLaunch(command, 'win32', { key: 'CODEX_HOME', path: 'relative' })).toBeNull()
  })

  it('命令不对就不跑', async () => {
    expect(loginLaunch([], 'win32')).toBeNull()
    expect(loginLaunch('codex login', 'win32')).toBeNull()
    expect(loginLaunch(['codex', 42], 'win32')).toBeNull()
    expect(loginLaunch(['relative.cmd', 'login'], 'win32')).toBeNull()
    expect((await openLoginWindow(null, 'win32', () => { throw new Error('不该跑') })).ok).toBe(false)
  })

  it('别的系统请你在终端里跑', async () => {
    const r = await openLoginWindow(['/usr/local/bin/codex', 'login'], 'darwin', () => { throw new Error('不该跑') })
    expect(r.ok).toBe(false)
    expect(r.error).toContain("'/usr/local/bin/codex' 'login'")
  })
})
