/**
 * 开场演出里「借鼠标」：她把你的光标挪着走（shared/intro.ts 第 3 幕）。只在你点了「借给你」以后才起来，演完就关。
 *
 * 为什么这样做：Electron 没有挪光标的接口，又不想为这几秒加一个原生模块。Windows 自带 PowerShell，
 * 起一个进程，里面只调 user32 的 SetCursorPos（挪光标），从标准输入一行一行读「x y」。
 * - 只能挪光标：不调点击、不调键盘，挪不了别的东西
 * - 坐标是物理像素：调用方先用 screen.dipToScreenPoint 换算好；进程这边把线程设成按显示器感知 DPI，坐标不会被系统再缩放一遍
 * - 起不来（不是 Windows、PowerShell 被禁、超时）就返回 null，演出改成「那我自己走过去」
 */
import { spawn, type ChildProcessWithoutNullStreams } from 'child_process'

export interface CursorHelper {
  /** 把光标放到这里（物理像素） */
  move(x: number, y: number): void
  close(): void
}

/** 进程里跑的脚本：只声明 SetCursorPos 和设 DPI 感知，读一行挪一次 */
export const CURSOR_SCRIPT = [
  'Add-Type -TypeDefinition @"',
  'using System; using System.Runtime.InteropServices;',
  'public static class AilaCursor {',
  '  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);',
  '  [DllImport("user32.dll")] public static extern IntPtr SetThreadDpiAwarenessContext(IntPtr value);',
  '}',
  '"@',
  '[AilaCursor]::SetThreadDpiAwarenessContext([IntPtr]-4) | Out-Null',
  '[Console]::Out.WriteLine("ready"); [Console]::Out.Flush()',
  'while ($null -ne ($line = [Console]::In.ReadLine())) {',
  '  $p = $line.Split(" ")',
  '  if ($p.Length -eq 2) { [AilaCursor]::SetCursorPos([int]$p[0], [int]$p[1]) | Out-Null }',
  '}'
].join('\n')

/** 一行「x y」：只认整数，别的不发 */
export function moveLine(x: number, y: number): string | null {
  if (!Number.isFinite(x) || !Number.isFinite(y)) return null
  return `${Math.round(x)} ${Math.round(y)}\n`
}

/** 起帮手进程；timeoutMs 内没说 ready 就当起不来 */
export function startCursorHelper(timeoutMs: number): Promise<CursorHelper | null> {
  if (process.platform !== 'win32') return Promise.resolve(null)
  return new Promise((resolve) => {
    let proc: ChildProcessWithoutNullStreams
    try {
      const encoded = Buffer.from(CURSOR_SCRIPT, 'utf16le').toString('base64')
      proc = spawn('powershell.exe', ['-NoProfile', '-NonInteractive', '-EncodedCommand', encoded], { windowsHide: true })
    } catch {
      return resolve(null)
    }
    let done = false
    const finish = (h: CursorHelper | null): void => {
      if (done) return
      done = true
      clearTimeout(timer)
      if (!h) proc.kill()
      resolve(h)
    }
    const timer = setTimeout(() => finish(null), timeoutMs)
    proc.on('error', () => finish(null))
    proc.on('exit', () => finish(null))
    proc.stdout.on('data', (d: Buffer) => {
      if (!d.toString().includes('ready')) return
      finish({
        move(x, y) {
          const line = moveLine(x, y)
          if (line && !proc.stdin.destroyed) proc.stdin.write(line)
        },
        close() {
          if (!proc.stdin.destroyed) proc.stdin.end()
          setTimeout(() => proc.kill(), 300)
        }
      })
    })
  })
}
