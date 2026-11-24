<script setup lang="ts">
import { t } from '../../i18n'
import { computed, onActivated, onUnmounted, ref } from 'vue'
import { describeDownload, IDLE_DOWNLOAD, type ModelDownloadState } from '../../../../shared/models'
import type { AiSettings, AiSettingsUpdate } from '../../../../shared/settings'
import { bridge } from '../../platform/bridge'
import Toggle from '../Toggle.vue'
import { store } from '../store'

const api = bridge!
const settings = ref<AiSettings | null>(null)
const offline = ref('')
const msg = ref<{ ok: boolean; text: string } | null>(null)
const busy = ref(false)

const engine = ref('melo')
const speaker = ref(0)
const speed = ref(1)
const wake = ref(0.25)
const silence = ref(0.4)
const language = ref<'zh' | 'auto'>('zh')

const SAMPLE = '……你好，我是艾拉。今天也请多指教。'

async function load(): Promise<void> {
  const res = await api.aiSettings()
  if (!res.ok || !res.data) {
    offline.value = res.error ?? t('读不到设置')
    return
  }
  offline.value = ''
  fill(res.data)
}

function fill(s: AiSettings): void {
  settings.value = s
  engine.value = s.voice.ttsEngine
  speaker.value = s.voice.ttsSpeaker
  speed.value = s.voice.ttsSpeed
  wake.value = s.voice.wakeThreshold
  silence.value = s.voice.endSilence
  language.value = s.voice.asrLanguage
}

const changes = computed<AiSettingsUpdate>(() => {
  const v = settings.value?.voice
  if (!v) return {}
  const u: AiSettingsUpdate = {}
  if (engine.value !== v.ttsEngine) u.ttsEngine = engine.value
  if (speaker.value !== v.ttsSpeaker) u.ttsSpeaker = speaker.value
  if (speed.value !== v.ttsSpeed) u.ttsSpeed = speed.value
  if (wake.value !== v.wakeThreshold) u.wakeThreshold = wake.value
  if (silence.value !== v.endSilence) u.endSilence = silence.value
  if (language.value !== v.asrLanguage) u.asrLanguage = language.value
  return u
})
const dirty = computed(() => Object.keys(changes.value).length > 0)

async function save(): Promise<void> {
  busy.value = true
  const res = await api.aiSettingsPut(changes.value)
  busy.value = false
  if (res.ok && res.data) {
    fill(res.data)
    msg.value = { ok: true, text: t('已保存。换了声音要等几秒重新加载') }
  } else {
    msg.value = { ok: false, text: res.error ?? t('没有保存成功') }
  }
}

/** 用现在保存的设置念一句给你听（新设置要先保存，加载好之后再试） */
async function listen(): Promise<void> {
  try {
    const wav = await api.synthesize(t(SAMPLE))
    if (!wav) return
    const ctx = new AudioContext()
    const source = ctx.createBufferSource()
    source.buffer = await ctx.decodeAudioData(wav)
    source.connect(ctx.destination)
    source.onended = () => void ctx.close()
    source.start()
  } catch {
    msg.value = { ok: false, text: t('试听失败：语音合成还没准备好（刚换了声音的话等几秒再试）') }
  }
}

/** 下载语音模型（安装包不带模型）：进度从主进程推过来 */
const download = ref<ModelDownloadState>(IDLE_DOWNLOAD)
const missing = computed(() => settings.value?.voice.modelsMissing ?? [])
const showDownload = computed(() => missing.value.length > 0 || download.value.running || download.value.finished || !!download.value.error)
const percent = computed(() => (download.value.totalMb ? Math.min(100, Math.round((download.value.doneMb / download.value.totalMb) * 100)) : 0))
// 浏览器里调试（npm run dev:web）没有桌面能力：不订阅
const stopModels = bridge?.onModelsState((s) => {
  const wasRunning = download.value.running
  download.value = s
  if (wasRunning && !s.running && s.finished) void load() // 下完、服务重启好了：重新读一遍「还缺哪些」
})
onUnmounted(() => stopModels?.())

async function startDownload(): Promise<void> {
  const res = await api.modelsDownload()
  if (!res.ok) download.value = { ...IDLE_DOWNLOAD, error: res.error ?? t('没开始下载') }
}

onActivated(async () => {
  await load()
  const s = await bridge?.modelsStatus()
  if (s) download.value = s
})
</script>

<template>
  <h2>{{ t('语音') }}</h2>
  <p class="lead">{{ t('她什么时候听、怎么说话。声音和识别都在这台电脑上处理，不会上传。') }}</p>

  <div class="card">
    <h3>{{ t('开关') }}</h3>
    <div class="row">
      <span class="label">{{ t('语音唤醒') }}</span>
      <div>
        <Toggle :model-value="store.voice.wake" @update:model-value="(v) => api.voiceSet('wake', v)" />
        <span class="hint">{{ t('打开后麦克风一直开着，等你说「你好艾拉」。每台电脑单独决定。') }}</span>
      </div>
    </div>
    <div class="row">
      <span class="label">{{ t('出声说话') }}</span>
      <div>
        <Toggle :model-value="store.voice.speak" @update:model-value="(v) => api.voiceSet('speak', v)" />
        <span class="hint">{{ t('关掉后她只打字。登录后会跟着账号同步。') }}</span>
      </div>
    </div>
  </div>

  <div v-if="offline" class="offline">
    {{ offline }}{{ t('（声音和识别的设置要 AI 服务开着才能改）') }}
    <button class="link" @click="load">{{ t('重试') }}</button>
  </div>

  <template v-else-if="settings">
    <div v-if="showDownload" class="card">
      <h3>{{ t('语音模型') }}</h3>
      <div class="row">
        <span class="label">{{ missing.length ? t('还没下载') : t('下载') }}</span>
        <div>
          <span class="hint">
            {{ t('唤醒、听写、说话都要语音模型，约 460MB，从 sherpa-onnx 的 GitHub 发布页下载，放在这台电脑上。下载好以后 AI 服务会自动重启一次。') }}
          </span>
          <div v-if="download.running && !download.finished" class="bar"><div :style="{ width: `${percent}%` }"></div></div>
          <div v-if="describeDownload(download)" class="hint" :class="{ error: !!download.error }">{{ describeDownload(download) }}</div>
          <button v-if="missing.length && !download.running" class="btn" @click="startDownload">
            {{ download.error ? t('再试一次') : t('下载语音模型') }}
          </button>
        </div>
      </div>
    </div>

    <div class="card">
      <h3>{{ t('她的声音') }}</h3>
      <div class="row">
        <span class="label">{{ t('语音引擎') }}</span>
        <div>
          <select v-model="engine">
            <option v-for="e in settings.voice.ttsEngines" :key="e.id" :value="e.id" :disabled="!e.installed">
              {{ e.name }}{{ e.installed ? '' : t('（模型没下载）') }}
            </option>
          </select>
        </div>
      </div>
      <div v-if="engine === 'kokoro'" class="row">
        <span class="label">{{ t('音色编号') }}</span>
        <div>
          <input v-model.number="speaker" type="number" min="0" max="102" style="width: 100px" />
          <span class="hint">{{ t('Kokoro 有 103 种音色，3–57 号大多是中文女声。') }}</span>
        </div>
      </div>
      <div class="row">
        <span class="label">{{ t('语速') }}</span>
        <div>
          <input v-model.number="speed" type="range" min="0.7" max="1.5" step="0.05" />
          <span class="value">{{ t('{n} 倍', { n: speed.toFixed(2) }) }}</span>
        </div>
      </div>
    </div>

    <div class="card">
      <h3>{{ t('听你说话') }}</h3>
      <div class="row">
        <span class="label">{{ t('唤醒灵敏度') }}</span>
        <div>
          <input v-model.number="wake" type="range" min="0.1" max="0.5" step="0.01" />
          <span class="value">{{ wake.toFixed(2) }}</span>
          <span class="hint">{{ t('越小越容易被叫醒，也越容易误唤醒；叫了几次都没反应就往小调。') }}</span>
        </div>
      </div>
      <div class="row">
        <span class="label">{{ t('停顿多久算说完') }}</span>
        <div>
          <input v-model.number="silence" type="range" min="0.25" max="1" step="0.05" />
          <span class="value">{{ t('{n} 秒', { n: silence.toFixed(2) }) }}</span>
          <span class="hint">{{ t('太短会把一句话切断，太长她反应慢。说话习惯慢的话调大一点。') }}</span>
        </div>
      </div>
      <div class="row">
        <span class="label">{{ t('识别语种') }}</span>
        <select v-model="language">
          <option value="zh">{{ t('中文（中英混着说也能认，短句更准）') }}</option>
          <option value="auto">{{ t('自动判断（经常说别的语言时用）') }}</option>
        </select>
      </div>
    </div>

    <div class="actions">
      <button class="btn primary" :disabled="busy || !dirty" @click="save">{{ t('保存') }}</button>
      <button class="btn" @click="listen">{{ t('试听') }}</button>
      <span v-if="msg" class="msg" :class="msg.ok ? 'ok' : 'error'">{{ msg.text }}</span>
    </div>
  </template>
</template>

<style scoped>
.value {
  margin-left: 10px;
  color: var(--label);
}

.bar {
  height: 6px;
  margin: 8px 0 4px;
  border-radius: 3px;
  background: var(--line, rgba(127, 127, 127, 0.25));
  overflow: hidden;
}

.bar > div {
  height: 100%;
  background: var(--accent, #a78bfa);
  transition: width 0.3s;
}

.hint.error {
  color: var(--danger, #e5484d);
}
</style>
