import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { MemoryOp, StoredMessage } from '../../shared/cloud'
import { MemoryKeeper, RETRY_MS, pendingWindow, type ExtractRequest } from './keeper'

function msgs(n: number): StoredMessage[] {
  return Array.from({ length: n }, (_, i) => ({
    id: `m${i}`,
    role: i % 2 === 0 ? 'user' : 'assistant',
    content: `第 ${i} 句`,
    createdAt: new Date(2026, 10, 19, 10, i).toISOString()
  }))
}

describe('这次要整理哪几句', () => {
  it('没有记录就是最近三轮；有记录就从那句之后补（前面带两句上下文），最多 18 句', () => {
    const all = msgs(40)
    expect(pendingWindow(all, '').map((m) => m.id)).toEqual(['m34', 'm35', 'm36', 'm37', 'm38', 'm39'])
    expect(pendingWindow(all, 'm35').map((m) => m.id)).toEqual(['m34', 'm35', 'm36', 'm37', 'm38', 'm39'])
    expect(pendingWindow(all, 'm10')).toHaveLength(18)
    expect(pendingWindow(all, 'gone')).toHaveLength(6) // 那句被删了：当没有记录
  })
})

describe('记忆管家', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  function setup(list: StoredMessage[]) {
    const calls: ExtractRequest[] = []
    let fail = true
    const applied: MemoryOp[][] = []
    const keeper = new MemoryKeeper({
      history: { visible: () => list } as never,
      memories: { list: () => [], apply: (ops: MemoryOp[]) => (applied.push(ops), ops.length > 0) } as never,
      extract: async (req) => {
        calls.push(req)
        if (fail) throw new Error('AI 服务返回 502')
        return [{ op: 'add', content: '用户喜欢猫', kind: 'preference', expiresAt: null } as MemoryOp]
      },
      onChanged: () => undefined
    })
    return { keeper, calls, applied, ok: () => (fail = false) }
  }

  it('没整理成就过一会儿再试，补的时候把攒下的几轮一起交过去', async () => {
    const list = msgs(6)
    const { keeper, calls, applied, ok } = setup(list)
    await keeper.run()
    expect(calls).toHaveLength(1)
    // 又聊了几句，服务还没好
    list.push(...msgs(10).slice(6))
    await vi.advanceTimersByTimeAsync(RETRY_MS[0])
    expect(calls).toHaveLength(2)
    ok()
    await vi.advanceTimersByTimeAsync(RETRY_MS[1])
    expect(calls).toHaveLength(3)
    expect(calls[2].messages).toHaveLength(6) // 从没整理过：最近三轮
    expect(applied).toHaveLength(1)
    // 整理成了就不再重试
    await vi.advanceTimersByTimeAsync(RETRY_MS[2] * 2)
    expect(calls).toHaveLength(3)
    keeper.stop()
  })

  it('试了三次还不行就等下一轮对话；整理过的不重复', async () => {
    const list = msgs(4)
    const { keeper, calls, ok } = setup(list)
    await keeper.run()
    for (const wait of RETRY_MS) await vi.advanceTimersByTimeAsync(wait)
    expect(calls).toHaveLength(4)
    await vi.advanceTimersByTimeAsync(RETRY_MS[2] * 3)
    expect(calls).toHaveLength(4)
    ok()
    keeper.afterReply()
    await vi.advanceTimersByTimeAsync(2000)
    expect(calls).toHaveLength(5)
    await keeper.run()
    expect(calls).toHaveLength(5) // 最后一句已经整理过
    list.push(...msgs(8).slice(4))
    await keeper.run()
    expect(calls[5].messages.map((m) => m.content)).toEqual(['第 2 句', '第 3 句', '第 4 句', '第 5 句', '第 6 句', '第 7 句'])
    keeper.stop()
  })
})
