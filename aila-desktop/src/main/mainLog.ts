/**
 * 主进程的日志文件（用户数据目录/logs/main.log）。
 *
 * 装好的艾拉没有控制台：以前主进程出错（比如聊天记录存不进文件）谁也看不到，只能猜。
 * 现在存盘出错、没接住的异常都写一行进来；超过 1MB 就把旧的挪成 main.old.log。写日志本身出错就算了，不影响别的
 */
import { appendFileSync, mkdirSync, renameSync, statSync } from 'fs'
import { join } from 'path'

const MAX_BYTES = 1024 * 1024

export interface MainLog {
  file: string
  write(line: string): void
}

export function createMainLog(dataDir: string): MainLog {
  const dir = join(dataDir, 'logs')
  const file = join(dir, 'main.log')
  return {
    file,
    write(line: string): void {
      try {
        mkdirSync(dir, { recursive: true })
        try {
          if (statSync(file).size > MAX_BYTES) renameSync(file, join(dir, 'main.old.log'))
        } catch {
          // 还没有日志文件
        }
        appendFileSync(file, `${new Date().toISOString()} ${line.replace(/\r?\n/g, '\n  ')}\n`, 'utf-8')
      } catch {
        // 日志写不进去也不能让别的出错
      }
    }
  }
}
