import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { MemoryOp, StoredMemory } from '../../shared/cloud'
import { ApiError } from './api'
import { HistoryStore } from './history'
import { MemoryKeeper, type ExtractRequest } from './keeper'
import { MemoryStore } from './memories'
import { SyncEngine } from './sync'

const add = (content: string, kind: StoredMemory['kind'] = 'other'): MemoryOp => ({ op: 'add', content, kind, expiresAt: null })

describe('MemoryStore', () => {
  it('执行整理出来的操作：新增、修改、删除', () => {
    const store = new MemoryStore(null)
    store.apply([add('用户住在北京', 'profile'), add('用户喜欢喝美式', 'preference')])
    const [a, b] = store.list().sort((x, y) => x.content.localeCompare(y.content))
    expect(store.dirty()).toHaveLength(2)

    store.apply([{ op: 'update', id: b.id, content: '用户搬到了上海', kind: 'profile', expiresAt: null }, { op: 'delete', id: a.id }])
    expect(store.list().map((m) => m.content)).toEqual(['用户搬到了上海'])
    // 从没传上去过的直接删掉，不留墓碑
    expect(store.dirty().map((m) => m.id)).toEqual([b.id])
  })

  it('传上去之后再删：留墓碑等同步，同步完才真正删掉', () => {
    const store = new MemoryStore(null)
    store.apply([add('用户养了一只橘猫')])
    const id = store.list()[0].id
    store.markSynced(id, 1)
    store.delete(id)
    expect(store.list()).toHaveLength(0)
    expect(store.dirty()).toEqual([expect.objectContaining({ id, deleted: true, content: '' })])
    store.markSynced(id, 2)
    expect(store.dirty()).toHaveLength(0)
  })

  it('云端删除优先：本地还没传上去的修改也作废', () => {
    const store = new MemoryStore(null)
    store.applyRemote({ id: 'm1', content: '用户下周三面试', kind: 'event', createdAt: '', updatedAt: '', seq: 3 })
    store.apply([{ op: 'update', id: 'm1', content: '用户下周四面试', kind: 'event', expiresAt: null }])
    expect(store.applyRemote({ id: 'm1', content: '', kind: 'event', createdAt: '', updatedAt: '', seq: 5, deleted: true })).toBe(true)
    expect(store.list()).toHaveLength(0)
    expect(store.dirty()).toHaveLength(0)
  })

  it('本地没传上去的修改不被云端旧版本覆盖；没有本地修改时取序号大的', () => {
    const store = new MemoryStore(null)
    store.applyRemote({ id: 'm1', content: '旧的', kind: 'other', createdAt: '', updatedAt: '', seq: 3 })
    store.apply([{ op: 'update', id: 'm1', content: '本地刚改的', kind: 'other', expiresAt: null }])
    expect(store.applyRemote({ id: 'm1', content: '别处改的', kind: 'other', createdAt: '', updatedAt: '', seq: 4 })).toBe(false)
    expect(store.list()[0].content).toBe('本地刚改的')

    store.markSynced('m1', 6)
    expect(store.applyRemote({ id: 'm1', content: '更旧的', kind: 'other', createdAt: '', updatedAt: '', seq: 5 })).toBe(false)
    expect(store.applyRemote({ id: 'm1', content: '更新的', kind: 'other', createdAt: '', updatedAt: '', seq: 7 })).toBe(true)
    expect(store.list()[0].content).toBe('更新的')
  })
})

describe('MemoryKeeper', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  function setup(ops: MemoryOp[] = [add('用户叫小王', 'profile')]) {
    const history = new HistoryStore(null)
    const memories = new MemoryStore(null)
    const requests: ExtractRequest[] = []
    const onChanged = vi.fn()
    const keeper = new MemoryKeeper({
      history,
      memories,
      extract: async (req) => {
        requests.push(req)
        return ops
      },
      onChanged
    })
    const say = (role: 'user' | 'assistant', content: string): void => {
      history.add({ id: crypto.randomUUID(), role, content, createdAt: new Date(Date.now() + history.size).toISOString() })
    }
    return { history, memories, keeper, requests, onChanged, say }
  }

  it('她说完后等一会儿再整理，连续几轮只整理一次', async () => {
    const { keeper, requests, onChanged, memories, say } = setup()
    say('user', '我叫小王')
    say('assistant', '你好，小王。')
    keeper.afterReply()
    await vi.advanceTimersByTimeAsync(1000)
    say('user', '今天好累')
    say('assistant', '辛苦了。')
    keeper.afterReply()
    await vi.advanceTimersByTimeAsync(1600)

    expect(requests).toHaveLength(1)
    expect(requests[0].messages.map((m) => m.content)).toEqual(['我叫小王', '你好，小王。', '今天好累', '辛苦了。'])
    expect(memories.list().map((m) => m.content)).toEqual(['用户叫小王'])
    expect(onChanged).toHaveBeenCalledTimes(1)
  })

  it('同一轮不重复整理；AI 服务出错时不影响下一轮', async () => {
    const { keeper, requests, say } = setup([])
    say('user', '你好')
    say('assistant', '你好。')
    await keeper.run()
    await keeper.run()
    expect(requests).toHaveLength(1)
  })

  it('把已有记忆一起发过去，让 AI 服务判断要不要改', async () => {
    const { keeper, requests, memories, say } = setup([])
    memories.apply([add('用户住在北京', 'profile')])
    say('user', '我搬到上海了')
    say('assistant', '……搬家辛苦了。')
    await keeper.run()
    expect(requests[0].memories).toEqual([expect.objectContaining({ content: '用户住在北京', kind: 'profile' })])
  })
})

describe('记忆同步', () => {
  it('上传本地的修改；云端说已经删了（410）就放弃', async () => {
    const memories = new MemoryStore(null)
    memories.apply([add('用户喜欢爬山'), add('用户喜欢游泳')])
    const [keep, gone] = memories.list()
    const put = vi.fn(async (m: StoredMemory) => {
      if (m.id === gone.id) throw new ApiError(410, 'MEMORY_DELETED', '已删除')
      return { seq: 9 }
    })
    const engine = new SyncEngine({
      api: {
        wsUrl: '',
        accessToken: async () => 't',
        sync: async () => ({ changes: [], nextSince: 0, hasMore: false, lastSeq: 0 }),
        upload: async () => ({ accepted: [], lastSeq: 0 }),
        putSetting: async () => ({ seq: 0 }),
        putMemory: put,
        deleteMemory: async () => ({ seq: 10 })
      },
      store: new HistoryStore(null),
      memories,
      getLastSeq: () => 0,
      setLastSeq: () => {},
      openSocket: () => {
        throw new Error('不用连接')
      }
    })
    ;(engine as unknown as { running: boolean }).running = true
    await engine.flush()
    expect(put).toHaveBeenCalledTimes(2)
    expect(memories.list().map((m) => m.id)).toEqual([keep.id])
    expect(memories.dirty()).toHaveLength(0)
  })
})
