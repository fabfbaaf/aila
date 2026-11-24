<script setup lang="ts">
import { t } from '../../i18n'
import { computed, onMounted, ref } from 'vue'
import { PERSONA_STYLES, type PersonaStyle } from '../../../../shared/settings'
import { bridge } from '../../platform/bridge'
import { store } from '../store'

const api = bridge!
const fileName = computed(() => store.modelPath?.split(/[\\/]/).pop() ?? '')

// 性格：存在 AI 服务的设置里（她说话用的提示词在那边），改了从下一句话起生效
const persona = ref<{ style: PersonaStyle; call: string; catchphrase: string } | null>(null)
const call = ref('')
const catchphrase = ref('')
const saved = ref('')
const personaError = ref('')

onMounted(async () => {
  const r = await api.aiSettings()
  if (r.ok && r.data?.persona) {
    persona.value = r.data.persona
    call.value = r.data.persona.call
    catchphrase.value = r.data.persona.catchphrase
  } else if (!r.ok) {
    personaError.value = r.error ?? t('读不到她的设置')
  }
})

async function savePersona(patch: { personaStyle?: PersonaStyle; personaCall?: string; personaCatchphrase?: string }): Promise<void> {
  personaError.value = ''
  const r = await api.aiSettingsPut(patch)
  if (!r.ok || !r.data?.persona) {
    personaError.value = r.error ?? t('没存上')
    return
  }
  persona.value = r.data.persona
  saved.value = t('存好了，从下一句话开始。')
  window.setTimeout(() => (saved.value = ''), 2500)
}
</script>

<template>
  <h2>{{ t('形象') }}</h2>
  <p class="lead">
    {{ t('她在桌面上的样子和性格。默认是她自己的原创形象；也可以换成别的 VRM 格式的 3D 模型（VRoid Studio 可以免费捏一个导出），或者 Live2D 模型（选模型文件夹里的') }} <code>.model3.json</code>{{ t('）。不想让她一直在桌面上，去「桌面」关掉「桌面上显示她」。') }}
  </p>

  <div class="card">
    <div class="row">
      <span class="label">{{ t('现在的模型') }}</span>
      <div>
        <span v-if="store.modelPath">{{ fileName }}</span>
        <span v-else>{{ t('艾拉自己的形象（原创，随程序自带）') }}</span>
        <span v-if="store.modelPath" class="hint">{{ store.modelPath }}</span>
      </div>
    </div>
    <div class="actions">
      <button class="btn primary" @click="api.modelChoose()">{{ t('选择模型（VRM 或 Live2D）…') }}</button>
      <button v-if="store.modelPath" class="btn" @click="api.modelBuiltin()">{{ t('换回艾拉自己的形象') }}</button>
      <span class="hint">{{ t('下次启动会自动加载上次选的模型。') }}</span>
    </div>
    <p class="hint">
      {{ t('Live2D 要先装 Live2D 的运行库：在') }} <code>aila-desktop</code> {{ t('里运行') }} <code>npm run setup:live2d</code>{{ t('（它是 Live2D 公司的专有软件，会先给你看许可协议）。Live2D 模型只认 Cubism 3 以后的格式（.model3.json）；手没有统一的参数，挥手、伸懒腰在 Live2D 上只有头和表情的动作。') }}
    </p>
  </div>

  <div class="card">
    <h3>{{ t('性格') }} <span class="state">{{ saved }}</span></h3>
    <p v-if="personaError" class="hint" style="color: var(--ui-bad-text)">{{ personaError }}</p>
    <template v-if="persona">
      <div class="row">
        <span class="label">{{ t('说话风格') }}</span>
        <div>
          <select
            :value="persona.style"
            style="width: 160px"
            @change="(e) => savePersona({ personaStyle: (e.target as HTMLSelectElement).value as PersonaStyle })"
          >
            <option v-for="s in PERSONA_STYLES" :key="s.id" :value="s.id">{{ s.name }}</option>
          </select>
          <span class="hint">{{ PERSONA_STYLES.find((s) => s.id === persona!.style)?.hint }}</span>
        </div>
      </div>
      <div class="row">
        <span class="label">{{ t('怎么叫你') }}</span>
        <div>
          <input v-model="call" maxlength="12" :placeholder="t('不填就叫「你」')" style="width: 160px" @change="savePersona({ personaCall: call })" />
          <span class="hint">{{ t('比如名字、外号。她会自然地用，不会每句都叫。') }}</span>
        </div>
      </div>
      <div class="row">
        <span class="label">{{ t('口头禅') }}</span>
        <div>
          <input v-model="catchphrase" maxlength="12" :placeholder="t('不填就没有')" style="width: 160px" @change="savePersona({ personaCatchphrase: catchphrase })" />
          <span class="hint">{{ t('偶尔在句尾带一句，十句里最多一两句。') }}</span>
        </div>
      </div>
      <p class="hint">{{ t('对话、日记、她给自己挑作品点子时都按这里来；编程任务里她还是认真干活的样子。') }}</p>
    </template>
  </div>

  <div class="card">
    <h3>{{ t('位置') }}</h3>
    <p class="hint" style="margin-top: 0">{{ t('按住她可以拖到屏幕上任何地方，靠近边缘会自动贴边。找不到她的时候：') }}</p>
    <div class="actions">
      <button class="btn" @click="api.resetPosition()">{{ t('让她回到右下角') }}</button>
    </div>
  </div>
</template>
