<script setup lang="ts">
import { t, joinList } from '../../i18n'
import { onActivated, onDeactivated, ref } from 'vue'
import type { CodeProject, FixStats } from '../../../../shared/settings'
import { bridge } from '../../platform/bridge'
import ProjectEvidence from './ProjectEvidence.vue'

const api = bridge!
const projects = ref<CodeProject[]>([])
const fixes = ref<FixStats | null>(null)
const offline = ref('')
const error = ref('')
const adding = ref(false)
const evidenceProject = ref('')
let timer = 0

async function load(): Promise<void> {
  const res = await api.codeList()
  if (!res.ok) {
    offline.value = res.error ?? t('连不上 AI 服务')
    return
  }
  offline.value = ''
  projects.value = res.data ?? []
  fixes.value = (await api.codeFixes()).data ?? null
  // 正在建索引：隔一会儿再看进度
  clearTimeout(timer)
  if (projects.value.some((p) => p.state === 'indexing')) timer = window.setTimeout(load, 1500)
}

async function add(): Promise<void> {
  error.value = ''
  adding.value = true
  const res = await api.codeAdd()
  adding.value = false
  if (res && !res.ok) error.value = res.error ?? t('添加失败')
  await load()
}

async function refresh(p: CodeProject): Promise<void> {
  await api.codeRefresh(p.id)
  await load()
}

/** 正在改哪个项目的忽略 */
const editing = ref('')
const ignoreText = ref('')

function editIgnore(p: CodeProject): void {
  editing.value = editing.value === p.id ? '' : p.id
  ignoreText.value = (p.ignore ?? []).join('\n')
}

async function saveIgnore(p: CodeProject): Promise<void> {
  error.value = ''
  const res = await api.codeIgnore(p.id, ignoreText.value.split(/\r?\n/))
  if (!res.ok) {
    error.value = res.error ?? t('没存上')
    return
  }
  editing.value = ''
  await load()
}

async function remove(p: CodeProject): Promise<void> {
  await api.codeRemove(p.id)
  await load()
}

function languages(p: CodeProject): string {
  const names: Record<string, string> = { python: 'Python', typescript: 'TS', javascript: 'JS', tsx: 'TSX', java: 'Java', vue: 'Vue' }
  return Object.entries(p.languages ?? {})
    .sort((a, b) => b[1] - a[1])
    .map(([k, n]) => `${names[k] ?? k} ${n}`)
    .join(' · ')
}

function state(p: CodeProject): string {
  if (p.state === 'indexing') return t('正在建索引……（第一次要算向量，几百个文件大约一分钟）')
  if (p.state === 'error') return t('出错了：{why}', { why: p.error ?? '' })
  if (!p.files) return t('还没建索引')
  const last = p.last ? t('，上次更新了 {n} 个文件，用了 {s} 秒', { n: p.last.parsed, s: p.last.seconds }) : ''
  return t('{files} 个文件、{chunks} 个函数和类', { files: p.files ?? 0, chunks: p.chunks ?? 0 }) + last
}

onActivated(load)
onDeactivated(() => clearTimeout(timer))
</script>

<template>
  <h2>{{ t('代码库') }}</h2>
  <p class="lead">
    {{ t('登记你的项目，她就能按函数找代码、看到报错时直接定位到出错的函数和调用它的地方。索引只建在这台电脑上，代码不上传；你改了代码，她下次查的时候会自动更新改过的文件。') }}
  </p>

  <div v-if="offline" class="offline">
    {{ offline }}
    <button class="link" @click="load">{{ t('重试') }}</button>
  </div>

  <div v-else class="card">
    <table v-if="projects.length" class="list">
      <tr v-for="p in projects" :key="p.id">
        <td>
          <div class="name">{{ p.name }}</div>
          <div class="hint path">{{ p.root }}</div>
          <div class="hint">{{ state(p) }}</div>
          <div v-if="p.files" class="hint">{{ languages(p) }}</div>
          <div v-if="p.ignoreFile?.length || p.ignore?.length" class="hint">
            {{ t('不看：') }}{{ joinList([...(p.ignoreFile ?? []), ...(p.ignore ?? [])]) }}
          </div>
          <button class="link evidence-toggle" type="button" :aria-expanded="evidenceProject === p.id"
            @click="evidenceProject = evidenceProject === p.id ? '' : p.id">{{ t('项目资料与问答') }}</button>
          <ProjectEvidence v-if="evidenceProject === p.id" :project-id="p.id" />
          <div v-if="editing === p.id" class="ignore-edit">
            <textarea v-model="ignoreText" rows="4" spellcheck="false" :placeholder="t('一行一个，比如：\ndocs/examples\nfixtures\n*.min.js')"></textarea>
            <span class="hint">
              {{ t('代码检索和星图都不看这些：从项目根目录算的路径、不带斜杠的文件夹名（哪一层都算）、带 * 的通配符。') }}
              <template v-if="p.ignoreFile?.length">{{ t('项目里的 .ailaignore 已经写了 {list}（要改就改那个文件）。', { list: joinList(p.ignoreFile) }) }}</template>
            </span>
            <div class="actions">
              <button class="btn primary" @click="saveIgnore(p)">{{ t('保存并重新扫描') }}</button>
              <button class="btn" @click="editing = ''">{{ t('算了') }}</button>
            </div>
          </div>
        </td>
        <td class="ops">
          <button class="link" @click="editIgnore(p)">{{ t('忽略……') }}</button>
          <button class="link" :disabled="p.state === 'indexing'" @click="refresh(p)">{{ t('重新扫描') }}</button>
          <button class="x" :title="t('不再登记这个项目（代码不动）')" @click="remove(p)">×</button>
        </td>
      </tr>
    </table>
    <p v-else class="empty">{{ t('还没有登记项目。支持 Python、Java、TypeScript / JavaScript、Vue。') }}</p>

    <p v-if="error" class="msg error">{{ error }}</p>
    <div class="actions">
      <button class="btn primary" :disabled="adding" @click="add">{{ adding ? t('选择文件夹……') : t('添加项目…') }}</button>
      <button v-if="projects.length" class="btn" @click="api.openWorkbench()">{{ t('看地图') }}</button>
      <span class="hint">{{ t('是 Git 仓库的话按 .gitignore 跳过不用的文件；node_modules、.venv 这类总是跳过。') }}</span>
    </div>
  </div>

  <div v-if="!offline" class="card">
    <h3>{{ t('她怎么改代码') }}</h3>
    <p class="hint" style="margin-top: 0">
      {{ t('你让她修的时候，她先把 diff 给你看，你点「好」后才动手：在新分支（aila/fix-…）上改，跑项目自带的测试，全过才改到你的代码里；没过就撤回，你的代码保持原样。测试命令按项目自动识别（pytest、npm test、mvnw test），不由大模型决定。项目必须是 Git 仓库。') }}
    </p>
    <p v-if="fixes && fixes.attempts" class="hint">
      {{ t('到现在修过 {a} 次：跑了测试的 {t} 次，通过 {p} 次，改到你代码里 {m} 次。', { a: fixes.attempts, t: fixes.tested, p: fixes.passed, m: fixes.merged }) }}
    </p>
  </div>
</template>

<style scoped>
.evidence-toggle {
  margin-top: 6px;
}

.ignore-edit {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-top: 8px;
}

.ignore-edit textarea {
  padding: 6px 8px;
  border: 1px solid var(--ui-line);
  border-radius: var(--ui-r-s);
  background: var(--ui-field);
  color: var(--ui-text);
  font-family: var(--ui-mono);
  font-size: 12px;
  resize: vertical;
}

.name {
  font-weight: 600;
}

.path {
  word-break: break-all;
}

.ops {
  width: 1%;
  white-space: nowrap;
  vertical-align: top;
}
</style>
