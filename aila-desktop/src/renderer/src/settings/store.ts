import { reactive } from 'vue'
import type { AccountStatus, StoredMemory } from '../../../shared/cloud'
import type { VoicePrefs } from '../../../shared/settings'
import { bridge } from '../platform/bridge'
import { t } from '../i18n'

/**
 * 设置窗口里各个分区共用的状态。主进程推过来的变化在这里统一接收（只注册一次监听），
 * 分区切来切去不会重复注册。
 */
export const store = reactive({
  account: null as AccountStatus | null,
  memories: [] as StoredMemory[],
  voice: { wake: false, speak: true, watch: false } as VoicePrefs,
  knowledgeBusy: '',
  modelPath: null as string | null
})

let started = false

export async function startStore(): Promise<void> {
  if (started || !bridge) return
  started = true
  const api = bridge
  api.onAccountStatus((s) => (store.account = s))
  api.onMemoryChanged((list) => (store.memories = list))
  api.onVoiceState((v) => (store.voice = v))
  api.onModelPath((p) => (store.modelPath = p))
  api.onKnowledgeBusy((n) => (store.knowledgeBusy = n > 1 ? `正在读 ${n} 个文件……` : t('正在读……（大文件要等一会儿）')))
  const [account, memories, voice, modelPath] = await Promise.all([
    api.accountStatus(),
    api.memoryList(),
    api.voiceGet(),
    api.modelPath()
  ])
  Object.assign(store, { account, memories, voice, modelPath })
}
