import { mkdirSync, mkdtempSync, readFileSync, rmSync, utimesSync, writeFileSync } from 'fs'
import { tmpdir } from 'os'
import { join } from 'path'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { HistoryStore } from './history'
import { MemoryStore } from './memories'
import { onSaveProblem, RETRY_MS, saveProblems, Saver } from './persist'

let dir = ''
beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), 'aila-persist-'))
})
afterEach(() => {
  vi.useRealTimers()
  onSaveProblem(() => {})
  rmSync(dir, { recursive: true, force: true })
})

describe('存盘', () => {
  it('聊天记录说完一句就写进文件', () => {
    vi.useFakeTimers()
    const file = join(dir, 'history.json')
    const h = new HistoryStore(file)
    h.add({ id: 'a', role: 'user', content: '我喜欢看美食视频', createdAt: '2026-11-26T01:00:00Z' })
    vi.advanceTimersByTime(400)
    expect(JSON.parse(readFileSync(file, 'utf-8'))).toHaveLength(1)
  })

  it('写不进去：记下错误、过一会儿再试，存上了就清掉', () => {
    vi.useFakeTimers()
    const seen: (string | null)[] = []
    onSaveProblem((p) => seen.push(p ? p.error : null))
    const file = join(dir, 'blocked', 'memories.json')
    mkdirSync(file, { recursive: true }) // 同名的文件夹占着：写不进去
    let text = '[1]'
    const saver = new Saver(file, () => text, 300)
    saver.schedule()
    vi.advanceTimersByTime(300)
    expect(saveProblems().map((p) => p.file)).toEqual([file])
    expect(seen).toHaveLength(1)

    rmSync(file, { recursive: true }) // 占着的东西没了
    text = '[2]'
    vi.advanceTimersByTime(RETRY_MS[0])
    expect(readFileSync(file, 'utf-8')).toBe('[2]')
    expect(saveProblems()).toEqual([])
    expect(seen.at(-1)).toBeNull()
  })
})

describe('取回丢了的记忆', () => {
  const remote = [
    { id: 'f1', content: '用户喜欢看美食类视频', kind: 'preference', expiresAt: null, updatedAt: '2026-11-26T02:00:00+08:00' },
    { id: 'f2', content: '用户常深夜还在用电脑', kind: 'preference', expiresAt: null, updatedAt: '2026-11-26T01:15:00+08:00' }
  ]

  it('本地是空的、AI 服务那边有比本地文件新的：取回来，标成要同步', () => {
    const file = join(dir, 'memories.json')
    writeFileSync(file, '[]')
    utimesSync(file, new Date('2026-11-19T02:52:00Z'), new Date('2026-11-19T02:52:00Z'))
    const store = new MemoryStore(file)
    store.load()
    expect(store.recover(remote)).toBe(2)
    expect(store.list().map((m) => m.content).sort()).toEqual(['用户喜欢看美食类视频', '用户常深夜还在用电脑'])
    expect(store.dirty()).toHaveLength(2)
  })

  it('本地有记忆就不动；比本地文件旧的不拿（「忘掉一切」以后不会被找回来）', () => {
    const withLocal = new MemoryStore(null)
    withLocal.apply([{ op: 'add', content: '用户叫小林', kind: 'profile', expiresAt: null }])
    expect(withLocal.recover(remote)).toBe(0)

    const file = join(dir, 'memories.json')
    writeFileSync(file, '[]') // 刚刚「忘掉一切」写的
    const forgot = new MemoryStore(file)
    forgot.load()
    expect(forgot.recover(remote)).toBe(0)
  })
})
