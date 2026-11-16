/**
 * 她什么时候该躲开。
 *
 * 判断「别人在全屏、在演示」用 Windows 自己给通知用的接口 SHQueryUserNotificationState：
 * 系统弹通知之前也是问它，全屏游戏、全屏视频、F11 的浏览器、PowerPoint 放映、锁屏都认得出来。
 * Electron 没有这个接口，又不想为它加原生依赖，所以开一个常驻的 PowerShell：
 * 每 1.5 秒问一次，变了才打印一行；它自己盯着艾拉的进程，艾拉没了它就退出（不会留下孤儿进程）。
 *
 * 开会共享屏幕判断不了（不一定全屏），所以还有手动的「免打扰」。
 */

import { spawn, type ChildProcess } from 'child_process'
import type { DesktopPrefs, FocusState, Presence } from '../shared/desktop'

/** SHQueryUserNotificationState 的返回值 → 我们关心的几种情况 */
export function focusFromQuns(state: number): FocusState {
  switch (state) {
    case 1: // QUNS_NOT_PRESENT：锁屏、屏保、快速切换用户
      return 'away'
    case 2: // QUNS_BUSY：全屏应用
    case 3: // QUNS_RUNNING_D3D_FULL_SCREEN：全屏 Direct3D（游戏）
    case 7: // QUNS_APP：全屏的应用商店应用
      return 'fullscreen'
    case 4: // QUNS_PRESENTATION_MODE：演示模式
      return 'presentation'
    default: // 5 QUNS_ACCEPTS_NOTIFICATIONS、6 QUNS_QUIET_TIME（装好系统后的第一个小时）
      return 'normal'
  }
}

/**
 * 她在不在桌面上、能不能主动说话：
 * - 免打扰：藏起来、不说话
 * - 全屏 / 演示（打开了「全屏时躲开」）：藏起来、不说话；没打开就留着但不说话（别在别人演示时出声）
 * - 锁屏：留着（回来时还在），不说话
 */
export function decidePresence(focus: FocusState, dnd: boolean, prefs: Pick<DesktopPrefs, 'hideOnFullscreen'>): Presence {
  if (dnd) return { visible: false, quiet: true, reason: 'dnd' }
  if (focus === 'fullscreen' || focus === 'presentation') {
    return { visible: !prefs.hideOnFullscreen, quiet: true, reason: focus }
  }
  if (focus === 'away') return { visible: true, quiet: true, reason: 'away' }
  return { visible: true, quiet: false, reason: 'normal' }
}

const SCRIPT = (parent: number): string => `
$ErrorActionPreference = 'Stop'
Add-Type -Namespace Aila -Name Quns -MemberDefinition '[DllImport("shell32.dll")] public static extern int SHQueryUserNotificationState(out int state);'
$last = -1
while ($true) {
  if (-not (Get-Process -Id ${parent} -ErrorAction SilentlyContinue)) { exit }
  $s = 0
  [void][Aila.Quns]::SHQueryUserNotificationState([ref]$s)
  if ($s -ne $last) { [Console]::Out.WriteLine($s); [Console]::Out.Flush(); $last = $s }
  Start-Sleep -Milliseconds 1500
}
`

/** 盯着系统的「能不能打扰」状态，变了就回调。只在 Windows 上有用，别的系统一直是 normal */
export class FocusWatch {
  private proc: ChildProcess | null = null
  private buffer = ''
  state: FocusState = 'normal'

  constructor(private onChange: (state: FocusState) => void) {}

  start(): void {
    if (process.platform !== 'win32' || this.proc) return
    const encoded = Buffer.from(SCRIPT(process.pid), 'utf16le').toString('base64')
    const proc = spawn('powershell.exe', ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', encoded], {
      windowsHide: true,
      stdio: ['ignore', 'pipe', 'ignore']
    })
    this.proc = proc
    proc.stdout?.setEncoding('utf8')
    proc.stdout?.on('data', (chunk: string) => {
      this.buffer += chunk
      const lines = this.buffer.split(/\r?\n/)
      this.buffer = lines.pop() ?? ''
      for (const line of lines) {
        const n = Number.parseInt(line.trim(), 10)
        if (Number.isNaN(n)) continue
        const next = focusFromQuns(n)
        if (next !== this.state) {
          this.state = next
          this.onChange(next)
        }
      }
    })
    proc.on('error', (err) => console.warn('判断全屏的 PowerShell 没开起来：', err.message))
    proc.on('exit', () => {
      if (this.proc === proc) this.proc = null
    })
  }

  stop(): void {
    this.proc?.kill()
    this.proc = null
  }
}
