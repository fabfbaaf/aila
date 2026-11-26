/**
 * 本地 JSON 文件（聊天记录、记忆）怎么写盘。
 *
 * 以前写盘出错时（文件被杀毒软件或别的程序占着、没权限……），异常抛在定时器里没人管：
 * 她在内存里记着，界面上看不出来，一重启就全没了。现在：
 * - 先写临时文件再改名；改名不行就直接覆盖原文件
 * - 还不行就记下错误、过一会儿再试（2 秒、10 秒，之后每分钟），运行状态页和日志里都看得到
 */
import { mkdirSync, renameSync, rmSync, writeFileSync } from 'fs'
import { dirname } from 'path'

export const RETRY_MS = [2_000, 10_000, 60_000]

export function writeJsonFile(file: string, text: string): void {
  mkdirSync(dirname(file), { recursive: true })
  const tmp = file + '.tmp'
  try {
    writeFileSync(tmp, text, 'utf-8')
    renameSync(tmp, file)
  } catch {
    try {
      rmSync(tmp, { force: true })
    } catch {
      // 临时文件删不掉也不要紧，下次会覆盖
    }
    // 改名失败多半是原文件被占着：直接覆盖试试；再失败就交给调用方
    writeFileSync(file, text, 'utf-8')
  }
}

export interface SaveProblem {
  file: string
  error: string
  at: string
}

const problems = new Map<string, SaveProblem>()
let listener: (problem: SaveProblem | null, file: string) => void = () => {}

/** 存盘出错、又存上了：problem 是 null 表示这个文件好了 */
export function onSaveProblem(l: (problem: SaveProblem | null, file: string) => void): void {
  listener = l
}

export function saveProblems(): SaveProblem[] {
  return [...problems.values()]
}

/** 防抖写盘，出错重试。serialize 在真正写的时候才调用，拿到的是最新的内容 */
export class Saver {
  private timer: ReturnType<typeof setTimeout> | null = null
  private failures = 0

  constructor(
    private file: string | null,
    private serialize: () => string,
    private delay: number
  ) {}

  schedule(): void {
    if (!this.file || this.timer) return
    const wait = this.failures ? RETRY_MS[Math.min(this.failures, RETRY_MS.length) - 1] : this.delay
    this.timer = setTimeout(() => {
      this.timer = null
      this.now()
    }, wait)
  }

  /** 马上写；返回写上了没有（没写上会自己过一会儿再试） */
  now(): boolean {
    if (this.timer) clearTimeout(this.timer)
    this.timer = null
    if (!this.file) return true
    try {
      writeJsonFile(this.file, this.serialize())
    } catch (err) {
      this.failures++
      const problem = { file: this.file, error: err instanceof Error ? err.message : String(err), at: new Date().toISOString() }
      problems.set(this.file, problem)
      listener(problem, this.file)
      this.schedule()
      return false
    }
    this.failures = 0
    if (problems.delete(this.file)) listener(null, this.file)
    return true
  }
}
