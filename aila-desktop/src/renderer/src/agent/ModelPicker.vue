<script setup lang="ts">
/**
 * 按供应商显示模型。选择后的开工与交接由任务页负责。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { isAgentEngine, type AgentEngine, type ModelGroup } from '../../../shared/agent'
import { t } from '../i18n'
import { modelName, samePick, type ModelPick } from './models'

const props = withDefaults(defineProps<{
  groups: ModelGroup[]
  value: ModelPick
  disabled?: boolean
  /** 在下面显示的一句说明（比如「换另一家会开一个新任务接着做」） */
  note?: string
  /** 只能选艾拉自己（电脑任务） */
  onlyAila?: boolean
  /** 往下弹（放在页面上方时） */
  down?: boolean
  /** 新任务的滚动对话框内，列表随表单展开。 */
  inline?: boolean
  loading?: boolean
  placeholder?: string
}>(), { disabled: false, note: '', onlyAila: false, down: false, inline: false, loading: false })

const emit = defineEmits<{
  pick: [pick: ModelPick]
  login: [engine: AgentEngine]
  install: [engine: AgentEngine]
  refresh: []
  open: []
}>()

const open = ref(false)
const root = ref<HTMLElement | null>(null)
const query = ref('')
const current = computed(() => props.placeholder || modelName(props.groups, props.value, t('默认模型')))
const account = computed(() => props.groups.find((g) => g.engine === props.value.engine)?.title ?? '')
const shown = computed(() => {
  const groups = props.onlyAila ? props.groups.filter((g) => g.engine === 'aila') : props.groups
  const match = query.value.trim().toLowerCase()
  if (!match) return groups
  return groups.flatMap((group) => {
    if ([group.title, group.agent, group.engine].some((part) => part?.toLowerCase().includes(match))) return [group]
    const models = group.models.filter((model) => [model.name, model.value].some((part) => part.toLowerCase().includes(match)))
    return models.length ? [{ ...group, models }] : []
  })
})

function toggle(): void {
  if (props.disabled) return
  open.value = !open.value
  if (open.value) {
    query.value = ''
    emit('open')
    const focus = props.inline ? '.model-search' : '.model.on, .model:not(:disabled)'
    void nextTick(() => root.value?.querySelector?.<HTMLElement>(focus)?.focus())
  }
}

function choose(group: ModelGroup, value: string): void {
  open.value = false
  const pick: ModelPick = { engine: group.engine, value }
  if (!samePick(pick, props.value)) emit('pick', pick)
  root.value?.querySelector?.<HTMLButtonElement>('.trigger')?.focus()
}

function onOutside(e: MouseEvent): void {
  if (open.value && root.value && !root.value.contains(e.target as Node)) open.value = false
}
function onKey(e: KeyboardEvent): void {
  if (e.key === 'Escape' && open.value) {
    e.stopPropagation()
    open.value = false
    root.value?.querySelector?.<HTMLButtonElement>('.trigger')?.focus()
  }
}
// 点外面收起（没有页面的时候不挂，比如测试里的自制渲染器）
const page = typeof document === 'undefined' ? null : document
onMounted(() => page?.addEventListener('mousedown', onOutside))
onBeforeUnmount(() => page?.removeEventListener('mousedown', onOutside))
</script>

<template>
  <div ref="root" class="model-picker" :class="{ inline }" @keydown="onKey">
    <button
      type="button"
      class="trigger"
      :disabled="disabled"
      :aria-expanded="open"
      :title="account ? `${account} · ${current}` : current"
      @click="toggle"
    >
      <span class="name">{{ value.engine.endsWith('-alt') && account ? account + ' · ' + current : current }}</span><span class="caret" aria-hidden="true">▾</span>
    </button>
    <div v-if="open" class="pop" :class="{ down }" role="group" :aria-label="t('选模型')">
      <input v-if="inline && groups.length" v-model="query" class="model-search" type="search"
        :aria-label="t('搜索模型或厂商')" :placeholder="t('搜索模型或厂商')" spellcheck="false" />
      <p v-if="!shown.length" class="why" role="status">
        {{ loading ? t('正在获取模型列表…') : query.trim() ? t('没有匹配的模型') : t('没拿到模型列表，点刷新再试。') }}
      </p>
      <section v-for="g in shown" :key="g.engine" class="group" :class="{ off: !g.ready }">
        <header>
          <b>{{ g.title }}</b>
          <span v-if="g.loading" class="dim">{{ t('正在问有哪些模型…') }}</span>
          <button v-if="isAgentEngine(g.engine) && g.can_install" class="refresh" type="button" @click="emit('install', g.engine)">{{ t('官方安装') }}</button>
          <button v-else-if="isAgentEngine(g.engine) && g.can_login && g.logged_in !== true" class="refresh" type="button" @click="emit('login', g.engine)">{{ t('登录') }}</button>
        </header>
        <p v-if="!g.ready && g.problem" class="why">{{ g.problem }}<template v-if="g.hints[0]"> · {{ g.hints[0] }}</template></p>
        <p v-else-if="g.installs_adapter" class="why">{{ t('第一次用时会自动装它的适配器（要联网，一两分钟）') }}</p>
        <button
          v-for="m in g.models"
          :key="m.value"
          type="button"
          class="model"
          :class="{ on: g.engine === value.engine && m.value === value.value }"
          :aria-pressed="g.engine === value.engine && m.value === value.value"
          :disabled="!g.ready"
          :title="m.description || m.name"
          @click="choose(g, m.value)"
        >
          <span>{{ m.name || m.value || t('默认模型') }}</span>
          <small v-if="m.description" class="dim">{{ m.description }}</small>
        </button>
      </section>
      <footer>
        <span v-if="note" class="dim">{{ note }}</span>
        <button type="button" class="refresh" @click="emit('refresh')">{{ t('刷新') }}</button>
      </footer>
    </div>
  </div>
</template>

<style scoped>
.model-picker {
  position: relative;
  display: inline-flex;
}

.trigger {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  max-width: 240px;
  padding: 4px 10px;
  border: 1px solid var(--line);
  border-radius: 999px;
  background: var(--ui-sunken);
  color: var(--text-2);
  font-size: 12.5px;
  cursor: pointer;
  transition: border-color var(--ui-fast), color var(--ui-fast), background var(--ui-fast);
}

.trigger:hover:enabled,
.trigger[aria-expanded='true'] {
  border-color: var(--ui-cyan-line);
  background: var(--ui-cyan-soft);
  color: var(--ui-cyan);
}

.trigger:disabled {
  opacity: 0.6;
  cursor: default;
}

.name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.caret {
  font-size: 10px;
}

.pop {
  position: absolute;
  right: 0;
  bottom: calc(100% + 6px);
  z-index: 30;
  width: min(340px, 86vw);
  max-height: min(440px, 70vh);
  overflow: auto;
  padding: 6px;
  border: 1px solid var(--line-strong);
  border-radius: var(--ui-r);
  background: var(--raised);
  box-shadow: var(--ui-shadow);
}

.pop.down {
  top: calc(100% + 6px);
  bottom: auto;
}

.model-picker.inline {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 6px;
  min-width: 0;
}

.inline .pop {
  position: static;
  width: 100%;
  box-sizing: border-box;
  max-height: min(320px, 44vh);
}

.model-search {
  box-sizing: border-box;
  width: 100%;
  padding: 6px 8px;
  border: 1px solid var(--line-strong);
  border-radius: var(--ui-r-s);
  background: var(--ui-sunken);
  color: inherit;
  font: inherit;
}

.group + .group {
  margin-top: 4px;
  padding-top: 4px;
  border-top: 1px solid var(--line);
}

.group header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 8px 2px;
  font-size: 12px;
}

.group header b {
  flex: 1;
  color: var(--text);
  font-weight: 600;
}

.why {
  margin: 0;
  padding: 0 8px 4px;
  color: var(--ui-faint);
  font-size: 11.5px;
}

.model {
  display: flex;
  width: 100%;
  flex-direction: column;
  align-items: flex-start;
  gap: 1px;
  padding: 6px 8px;
  border: 1px solid transparent;
  border-radius: var(--ui-r-s);
  background: none;
  color: var(--text-2);
  font-size: 13px;
  text-align: left;
  cursor: pointer;
}

.model:hover:enabled,
.model:focus-visible {
  background: var(--ui-sunken);
  color: var(--text);
  outline: none;
}

.model.on {
  border-color: var(--accent-line);
  background: var(--accent-bg);
  color: var(--accent);
}

.model:disabled {
  opacity: 0.5;
  cursor: default;
}

.model small {
  font-size: 11px;
}

.dim {
  color: var(--dim);
  font-size: 11.5px;
}

.login,
.refresh {
  padding: 2px 10px;
  border: 1px solid var(--accent-line);
  border-radius: 999px;
  background: var(--accent-bg);
  color: var(--accent);
  font-size: 12px;
  cursor: pointer;
}

.refresh {
  border-color: var(--line);
  background: none;
  color: var(--text-2);
}

.pop footer {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 4px;
  padding: 6px 8px 2px;
  border-top: 1px solid var(--line);
}

.pop footer .dim {
  flex: 1;
}

.pop footer .refresh {
  margin-left: auto;
}
</style>
