/**
 * 端到端：连真的同步服务器，两台「设备」登录同一个账号。
 * 先启动服务器（aila-server 里 ./mvnw spring-boot:test-run），再：
 *   AILA_SERVER_E2E=http://127.0.0.1:8790 npx vitest run sync.e2e
 * 没设置这个环境变量时跳过。
 */
import { randomUUID } from 'crypto'
import { describe, expect, it } from 'vitest'
import WebSocket from 'ws'
import type { RemoteChanges, StoredMessage } from '../../shared/cloud'
import { CloudApi } from './api'
import { HistoryStore } from './history'
import { SyncEngine, type SocketLike } from './sync'

const SERVER = process.env['AILA_SERVER_E2E']

function device(api: CloudApi) {
  const store = new HistoryStore(null)
  let lastSeq = 0
  const received: RemoteChanges[] = []
  const engine = new SyncEngine({
    api,
    store,
    getLastSeq: () => lastSeq,
    setLastSeq: (s) => (lastSeq = s),
    openSocket: (url, token) => new WebSocket(url, { headers: { Authorization: `Bearer ${token}` } }) as unknown as SocketLike
  })
  engine.onRemote = (c) => received.push(c)
  return { api, store, engine, received, lastSeq: () => lastSeq }
}

const say = (content: string): StoredMessage => ({ id: randomUUID(), role: 'user', content, createdAt: new Date().toISOString() })

async function waitFor(check: () => boolean, ms = 5000): Promise<void> {
  const until = Date.now() + ms
  while (!check()) {
    if (Date.now() > until) throw new Error('等待超时')
    await new Promise((r) => setTimeout(r, 50))
  }
}

describe.skipIf(!SERVER)('和真服务器同步', () => {
  it('两台电脑互相看到对方说的话，离线期间的也不丢', async () => {
    const email = `e2e-${randomUUID().slice(0, 8)}@test.aila`
    const homeApi = new CloudApi(SERVER!)
    await homeApi.register({ email, password: 'correct-horse', deviceId: 'home', deviceName: '家里' })
    const workApi = new CloudApi(SERVER!)
    await workApi.login({ email, password: 'correct-horse', deviceId: 'work', deviceName: '公司' })

    const home = device(homeApi)
    const work = device(workApi)
    home.engine.start()
    work.engine.start()
    await waitFor(() => home.engine.status === 'online' && work.engine.status === 'online')

    // 家里说一句，公司马上收到
    home.store.add(say('早上好'))
    home.engine.localChanged()
    await waitFor(() => work.store.visible().length === 1)
    expect(work.store.visible()[0].content).toBe('早上好')

    // 公司的电脑断开期间，家里又说了三句
    work.engine.stop()
    for (const t of ['一', '二', '三']) home.store.add(say(t))
    home.engine.localChanged()
    await waitFor(() => home.store.unsynced().length === 0)

    work.engine.start() // 重新连上：hello 带着最新序号，发现落后了就去拉
    await waitFor(() => work.store.visible().length === 4)
    expect(work.store.visible().map((m) => m.content)).toEqual(['早上好', '一', '二', '三'])
    expect(work.lastSeq()).toBe(4)

    // 家里断网时说的话先存在本地，连上后补传，公司也收到
    home.engine.stop()
    home.store.add(say('断网时说的'))
    expect(home.store.unsynced()).toHaveLength(1)
    home.engine.start()
    await waitFor(() => work.store.visible().length === 5)

    home.engine.stop()
    work.engine.stop()
  }, 30_000)
})
