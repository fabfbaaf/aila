<script setup lang="ts">
/**
 * 设置 → 编程 →「定时任务」：到点她自己开一个编程任务做；或者先跑项目的测试，没过才开任务查原因。
 * 规则在 AI 服务那边（aila-ai/app/runtime/schedules.py）：项目只认登记过的，能改东西的一定开在新分支上。
 */
import { t } from '../../i18n'
import { onMounted, ref } from 'vue'
import type { AiResult, ScheduleInput, ScheduleItem } from '../../../../shared/settings'
import { bridge } from '../../platform/bridge'

const items = ref<ScheduleItem[]>([])
const projects = ref<{ name: string; path: string }[]>([])
const error = ref('')
const notice = ref('')
const busy = ref('')
const adding = ref(false)
const WEEK = [t('周一'), t('周二'), t('周三'), t('周四'), t('周五'), t('周六'), t('周日')]

function blank(): ScheduleInput {
  return { name: '', project: projects.value[0]?.path ?? '', prompt: '', kind: 'daily', time: '09:00', days: [0, 1, 2, 3, 4], hours: 6, action: 'tests', mode: 'read_only' }
}
const form = ref<ScheduleInput>(blank())

function fail<T>(r: AiResult<T>): boolean {
  if (r.ok) return false
  error.value = r.error ?? t('出错了。')
  return true
}

async function load(): Promise<void> {
  if (!bridge) return
  const r = await bridge.schedulesList()
  if (fail(r) || !r.data) return
  items.value = r.data.items
  projects.value = r.data.projects
  error.value = ''
}

function toggleDay(d: number): void {
  const days = form.value.days
  form.value.days = days.includes(d) ? days.filter((x) => x !== d) : [...days, d].sort()
}

async function save(): Promise<void> {
  if (!bridge) return
  notice.value = ''
  const r = await bridge.schedulesAdd({ ...form.value, days: [...form.value.days] })
  if (fail(r)) return
  adding.value = false
  notice.value = t('设好了。')
  await load()
}

async function act(id: string, fn: () => Promise<AiResult<unknown>>, done = ''): Promise<void> {
  busy.value = id
  const r = await fn()
  busy.value = ''
  if (fail(r)) return
  notice.value = done
  await load()
}

function when(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString() : t('停着')
}

function startAdd(): void {
  form.value = blank()
  adding.value = true
}

onMounted(load)
</script>

<template>
  <div class="card schedules" :aria-busy="Boolean(busy)">
    <h3>{{ t('定时任务') }}</h3>
    <p class="hint">{{ t('到点她自己开一个任务做，比如每天早上跑一遍测试、没过就查原因。只在登记过的项目里；能改东西的一定开在新分支上，你看过、合并了才进你的代码。艾拉开着时才跑，关机错过的下次启动补跑一次。') }}</p>
    <p v-if="error" class="msg error" role="alert">{{ error }}</p>
    <p v-if="notice" class="msg ok" role="status">{{ notice }}</p>

    <ul v-if="items.length" class="list">
      <li v-for="s in items" :key="s.id" :class="{ off: !s.enabled }">
        <div class="main">
          <b>{{ s.name }}</b>
          <span class="hint">{{ s.describe }}</span>
          <span class="hint">{{ t('下次：{when}', { when: when(s.next) }) }} · {{ t('上次：{result}', { result: s.last_result || t('还没跑过') }) }}</span>
        </div>
        <div class="ops">
          <label class="switch">
            <input type="checkbox" :checked="s.enabled" :disabled="busy === s.id" @change="act(s.id, () => bridge!.schedulesChange(s.id, { enabled: !s.enabled }))" />
            <span>{{ t('开着') }}</span>
          </label>
          <button class="btn" :disabled="busy === s.id" @click="act(s.id, () => bridge!.schedulesRun(s.id), t('跑了一次，结果在「上次」里。'))">
            {{ busy === s.id ? t('正在跑…') : t('现在跑一次') }}
          </button>
          <button class="btn danger" :disabled="busy === s.id" @click="act(s.id, () => bridge!.schedulesDelete(s.id), t('删掉了。'))">{{ t('删掉') }}</button>
        </div>
      </li>
    </ul>
    <p v-else class="hint">{{ t('还没有定时任务。也可以在对话里跟她说「每天早上 9 点帮我跑一下测试」。') }}</p>

    <button v-if="!adding" class="btn" :disabled="!projects.length" @click="startAdd">{{ t('加一个') }}</button>
    <p v-if="!projects.length" class="hint">{{ t('先登记一个项目或者在里面做过任务，才能设定时任务。') }}</p>

    <div v-if="adding" class="form">
      <input v-model="form.name" maxlength="40" :placeholder="t('名字，比如：早上跑测试')" />
      <select v-model="form.project">
        <option v-for="p in projects" :key="p.path" :value="p.path">{{ p.name }}</option>
      </select>
      <textarea v-model="form.prompt" rows="3" :placeholder="t('到点了让她做什么')"></textarea>
      <div class="row">
        <select v-model="form.kind">
          <option value="daily">{{ t('每天几点') }}</option>
          <option value="interval">{{ t('每隔几小时') }}</option>
        </select>
        <input v-if="form.kind === 'daily'" v-model="form.time" class="time" placeholder="09:00" />
        <input v-else v-model.number="form.hours" class="time" type="number" min="1" max="168" />
      </div>
      <div v-if="form.kind === 'daily'" class="days">
        <label v-for="(name, d) in WEEK" :key="d">
          <input type="checkbox" :checked="form.days.includes(d)" @change="toggleDay(d)" />{{ name }}
        </label>
      </div>
      <label class="check">
        <input type="checkbox" :checked="form.action === 'tests'" @change="form.action = form.action === 'tests' ? 'run' : 'tests'" />
        {{ t('先跑项目的测试，没过才让她做（测试命令按项目自动识别）') }}
      </label>
      <select v-model="form.mode">
        <option value="read_only">{{ t('只读：只看、只报告') }}</option>
        <option value="ask">{{ t('先问再改：在新分支上改，要改时问你（手机上能点）') }}</option>
        <option value="auto">{{ t('工作区内自动：在新分支上改，不问（危险命令还是问）') }}</option>
      </select>
      <div class="row">
        <button class="btn primary" @click="save">{{ t('保存') }}</button>
        <button class="btn" @click="adding = false">{{ t('取消') }}</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.list {
  margin: 8px 0;
  padding: 0;
  list-style: none;
}

.list li {
  display: flex;
  gap: 12px;
  align-items: flex-start;
  padding: 8px 0;
  border-top: 1px solid var(--ui-line, rgba(255, 255, 255, 0.08));
}

.list li.off {
  opacity: 0.6;
}

.main {
  display: flex;
  flex-direction: column;
  gap: 2px;
  flex: 1;
  min-width: 0;
}

.ops {
  display: flex;
  gap: 6px;
  align-items: center;
  flex-wrap: wrap;
}

.switch,
.check,
.days label {
  display: inline-flex;
  gap: 4px;
  align-items: center;
}

.form {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 10px;
}

.row,
.days {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  align-items: center;
}

.time {
  width: 90px;
}
</style>
