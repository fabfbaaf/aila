/**
 * 登录本机的编程 Agent（ChatGPT 的 Codex、Claude Code……）：开一个终端窗口跑它的官方登录命令（codex login、claude auth login……）。
 * 账号密码在官方程序里（多半会打开浏览器）填，艾拉不经手、不读它的令牌文件。
 * 命令是 AI 服务按这台电脑上找到的程序给的（aila-ai/app/runtime/localagents.py 的 login_command），不收页面传来的命令。
 * Windows 单独打开一个交互式控制台，让厂商的程序可以读键盘并显示登录提示。
 */
import { spawn } from 'node:child_process'
import { posix, win32 } from 'node:path'
import type { AiResult } from '../shared/settings'
import { tm } from './lang'

const psQuote = (value: string): string => "'" + value.replace(/'/g, "''") + "'"
const encode = (value: string): string => Buffer.from(value, 'utf16le').toString('base64')
const shQuote = (value: string): string => "'" + value.replaceAll("'", "'\"'\"'") + "'"

const profileOverrides = {
  CODEX_HOME: ['OPENAI_API_KEY', 'CODEX_API_KEY', 'CODEX_ACCESS_TOKEN'],
  CLAUDE_CONFIG_DIR: ['ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'CLAUDE_CODE_OAUTH_TOKEN',
    'CLAUDE_CODE_USE_BEDROCK', 'CLAUDE_CODE_USE_VERTEX', 'CLAUDE_CODE_USE_FOUNDRY', 'ANTHROPIC_BASE_URL']
} as const
type LoginProfile = { key: keyof typeof profileOverrides; path: string }

/** Windows 用单独的 PowerShell 控制台运行厂商命令，保留输入和输出。 */
export function loginLaunch(command: unknown, platform: NodeJS.Platform, profile?: unknown): { file: string; args: string[] } | { manual: string } | null {
  if (!Array.isArray(command) || !command.length || command.length > 8
    || command.some((part) => typeof part !== 'string' || !part || part.length > 1000 || /[\r\n\0]/.test(part))) return null
  let selected: LoginProfile | undefined
  if (profile !== undefined) {
    if (!profile || typeof profile !== 'object' || !('key' in profile) || !('path' in profile) ||
      (profile.key !== 'CODEX_HOME' && profile.key !== 'CLAUDE_CONFIG_DIR') ||
      typeof profile.path !== 'string' || profile.path.length > 1000 || /[\r\n\0]/.test(profile.path) ||
      !(platform === 'win32' ? win32 : posix).isAbsolute(profile.path)) return null
    selected = { key: profile.key, path: profile.path }
  }
  const [program, ...args] = command as string[]
  if (platform !== 'win32') {
    const prefix = selected
      ? ['env', ...profileOverrides[selected.key].flatMap((key) => ['-u', key]), shQuote(selected.key + '=' + selected.path)]
      : []
    return { manual: [...prefix, ...[program, ...args].map(shQuote)].join(' ') }
  }
  if (!win32.isAbsolute(program) || !/\.(exe|cmd|bat)$/i.test(program)) return null
  const setup = selected
    ? [
        ...profileOverrides[selected.key].map((key) => "[Environment]::SetEnvironmentVariable('" + key + "', $null, 'Process')"),
        '$env:' + selected.key + ' = ' + psQuote(selected.path)
      ]
    : []
  const run = encode([...setup, '& ' + [program, ...args].map(psQuote).join(' ')].join('\n'))
  const launch = `Start-Process -FilePath (Join-Path $PSHOME 'powershell.exe') -ArgumentList '-NoLogo','-NoProfile','-NoExit','-EncodedCommand','${run}' -WindowStyle Normal -ErrorAction Stop`
  return { file: 'powershell.exe', args: ['-NoProfile', '-NonInteractive', '-EncodedCommand', encode(launch)] }
}

export async function openLoginWindow(command: unknown, platform: NodeJS.Platform = process.platform,
  start: (file: string, args: string[]) => void | Promise<void> = startWindow,
  profile?: unknown): Promise<AiResult<{ started: boolean }>> {
  const launch = loginLaunch(command, platform, profile)
  if (!launch) return { ok: false, error: tm('没拿到它的登录命令。') }
  if ('manual' in launch) return { ok: false, error: tm('在终端里运行：{command}', { command: launch.manual }) }
  try {
    await start(launch.file, launch.args)
  } catch (err) {
    return { ok: false, error: tm('登录窗口没打开：{why}', { why: err instanceof Error ? err.message : String(err) }) }
  }
  return { ok: true, data: { started: true } }
}

function startWindow(file: string, args: string[]): Promise<void> {
  const env = { ...process.env }
  delete env['ELECTRON_RUN_AS_NODE']
  return new Promise((resolve, reject) => {
    const child = spawn(file, args, { stdio: 'ignore', windowsHide: true, env })
    child.once('error', reject)
    child.once('exit', (code) => {
      if (code === 0) resolve()
      else reject(new Error(String(code ?? '')))
    })
  })
}
