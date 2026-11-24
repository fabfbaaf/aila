<script setup lang="ts">
import { t } from '../../i18n'
import { onActivated, ref } from 'vue'
import type { AiSettings, AiSettingsUpdate } from '../../../../shared/settings'
import { bridge } from '../../platform/bridge'

const api = bridge!
const settings = ref<AiSettings | null>(null)
const offline = ref('')

// 表单：密钥框留空表示不改
const baseUrl = ref('')
const model = ref('')
const coderModel = ref('')
const coderFastModel = ref('')
const apiKey = ref('')
const provider = ref<'bocha' | 'tavily' | 'none'>('bocha')
const searchKey = ref('')

const llmMsg = ref<{ ok: boolean; text: string } | null>(null)
const searchMsg = ref<{ ok: boolean; text: string } | null>(null)
const busy = ref(false)

const MODELS = ['deepseek-flash', 'deepseek-v4-pro', 'qwen-plus', 'qwen-max', 'moonshot-v1-8k', 'glm-4-plus']

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
  baseUrl.value = s.llm.baseUrl
  model.value = s.llm.model
  coderModel.value = s.llm.coderModel ?? ''
  coderFastModel.value = s.llm.coderFastModel ?? ''
  provider.value = s.search.provider
  apiKey.value = ''
  searchKey.value = ''
}

async function save(update: AiSettingsUpdate, msg: typeof llmMsg): Promise<boolean> {
  busy.value = true
  const res = await api.aiSettingsPut(update)
  busy.value = false
  if (!res.ok || !res.data) {
    msg.value = { ok: false, text: res.error ?? t('没有保存成功') }
    return false
  }
  fill(res.data)
  msg.value = { ok: true, text: t('已保存，马上生效') }
  return true
}

async function saveLlm(): Promise<void> {
  const update: AiSettingsUpdate = {
    llmBaseUrl: baseUrl.value.trim(),
    llmModel: model.value.trim(),
    coderModel: coderModel.value.trim(),
    coderFastModel: coderFastModel.value.trim()
  }
  if (apiKey.value.trim()) update.llmApiKey = apiKey.value.trim()
  if (await save(update, llmMsg)) await test()
}

async function clearKey(): Promise<void> {
  await save({ llmApiKey: '' }, llmMsg)
  llmMsg.value = { ok: true, text: t('密钥已清除，现在用的是内置的假模型') }
}

async function test(): Promise<void> {
  busy.value = true
  llmMsg.value = { ok: true, text: t('正在测试……') }
  const res = await api.aiSettingsTest()
  busy.value = false
  llmMsg.value = res.ok && res.data ? { ok: res.data.ok, text: res.data.message } : { ok: false, text: res.error ?? t('测试失败') }
}

async function saveSearch(): Promise<void> {
  const update: AiSettingsUpdate = { searchProvider: provider.value }
  if (searchKey.value.trim()) update.searchApiKey = searchKey.value.trim()
  await save(update, searchMsg)
}

onActivated(load) // 第一次显示和每次切回来都会触发（KeepAlive）
</script>

<template>
  <h2>{{ t('对话') }}</h2>
  <p class="lead">{{ t('她用哪个大模型思考、用什么搜索引擎上网。保存后立刻生效，不用重启。') }}</p>

  <div v-if="offline" class="offline">
    {{ offline }}
    <button class="link" @click="load">{{ t('重试') }}</button>
  </div>

  <template v-else-if="settings">
    <div class="card">
      <h3>{{ t('大模型') }}</h3>
      <div class="row">
        <span class="label">{{ t('服务地址') }}</span>
        <div>
          <input v-model="baseUrl" type="text" spellcheck="false" />
          <span class="hint">{{ t('兼容 OpenAI 接口的都可以：DeepSeek、通义千问、Kimi、智谱……') }}</span>
        </div>
      </div>
      <div class="row">
        <span class="label">{{ t('模型') }}</span>
        <div>
          <input v-model="model" type="text" list="aila-models" spellcheck="false" />
          <datalist id="aila-models">
            <option v-for="m in MODELS" :key="m" :value="m" />
          </datalist>
        </div>
      </div>
      <div class="row">
        <span class="label">{{ t('编程用的模型') }}</span>
        <div>
          <input v-model="coderModel" type="text" list="aila-models" spellcheck="false" :placeholder="t('留空 = 和上面一样')" />
          <span class="hint">{{ t('任务页和终端里的 aila 用它。写代码可以换一个更强的模型，聊天还用便宜的') }}</span>
        </div>
      </div>
      <div class="row">
        <span class="label">{{ t('查资料用的模型') }}</span>
        <div>
          <input v-model="coderFastModel" type="text" list="aila-models" spellcheck="false" :placeholder="t('留空 = 和编程用的一样')" />
          <span class="hint">{{ t('她派助手去翻代码、查问题时用。只读不改，用便宜快的就够了') }}</span>
        </div>
      </div>
      <div class="row">
        <span class="label">{{ t('密钥') }}</span>
        <div>
          <input
            v-model="apiKey"
            type="password"
            autocomplete="off"
            :placeholder="settings.llm.apiKeySet ? t('已设置（不显示）。要换就填新的') : t('还没有设置——不填的话她只会几句固定回复')"
          />
          <span class="hint">
            {{ t('只保存在这台电脑的 aila-ai/.env 里，界面上不会再显示出来。') }}
            <button v-if="settings.llm.apiKeySet" class="link" @click="clearKey">{{ t('清除密钥') }}</button>
          </span>
        </div>
      </div>
      <div class="actions">
        <button class="btn primary" :disabled="busy" @click="saveLlm">{{ t('保存并测试') }}</button>
        <button class="btn" :disabled="busy" @click="test">{{ t('测试连接') }}</button>
        <span v-if="llmMsg" class="msg" :class="llmMsg.ok ? 'ok' : 'error'">{{ llmMsg.text }}</span>
      </div>
    </div>

    <div class="card">
      <h3>{{ t('联网搜索') }}</h3>
      <div class="row">
        <span class="label">{{ t('搜索服务') }}</span>
        <select v-model="provider">
          <option value="bocha">{{ t('博查（国内，中文结果好）') }}</option>
          <option value="tavily">{{ t('Tavily（海外，每月 1000 次免费）') }}</option>
          <option value="none">{{ t('不用（只读你给的链接）') }}</option>
        </select>
      </div>
      <div v-if="provider !== 'none'" class="row">
        <span class="label">{{ t('密钥') }}</span>
        <input
          v-model="searchKey"
          type="password"
          autocomplete="off"
          :placeholder="settings.search.apiKeySet ? t('已设置（不显示）。要换就填新的') : t('还没有设置——没有密钥时她不能自己搜')"
        />
      </div>
      <div class="actions">
        <button class="btn primary" :disabled="busy" @click="saveSearch">{{ t('保存') }}</button>
        <span v-if="searchMsg" class="msg" :class="searchMsg.ok ? 'ok' : 'error'">{{ searchMsg.text }}</span>
      </div>
    </div>
  </template>
</template>
