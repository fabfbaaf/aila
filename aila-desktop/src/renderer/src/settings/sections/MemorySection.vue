<script setup lang="ts">
import { computed, onActivated, onMounted, ref } from 'vue'
import { MEMORY_MAX, type MemoryEpisode, type MemoryKind, type StoredMemory } from '../../../../shared/cloud'
import { bridge } from '../../platform/bridge'
import { store } from '../store'

const api = bridge!
const confirming = ref(false)

const GROUPS: { kind: MemoryKind; name: string }[] = [
  { kind: 'profile', name: '关于你' },
  { kind: 'preference', name: '喜好和习惯' },
  { kind: 'event', name: '日程' },
  { kind: 'other', name: '其他' }
]

const groups = computed(() =>
  GROUPS.map((g) => ({ ...g, items: store.memories.filter((m) => m.kind === g.kind) })).filter((g) => g.items.length)
)

function until(m: StoredMemory): string {
  if (!m.expiresAt) return ''
  const d = new Date(m.expiresAt)
  return `到 ${d.getMonth() + 1} 月 ${d.getDate()} 日`
}

function forget(id: string): void {
  api.memoryDelete(id)
}

// ---------- 自己加、改 ----------

interface Draft {
  content: string
  kind: MemoryKind
  /** 日程：到哪天（YYYY-MM-DD） */
  until: string
}

const adding = ref<Draft>({ content: '', kind: 'profile', until: '' })
const addMsg = ref('')
const editingId = ref('')
const editing = ref<Draft>({ content: '', kind: 'other', until: '' })
const editMsg = ref('')

/** 日程的日期按那天的最后一刻算过期 */
function expiresOf(d: Draft): string | null {
  return d.kind === 'event' && d.until ? new Date(`${d.until}T23:59:59`).toISOString() : null
}

async function add(): Promise<void> {
  const r = await api.memoryEdit({ op: 'add', content: adding.value.content, kind: adding.value.kind, expiresAt: expiresOf(adding.value) })
  if (!r.ok) {
    addMsg.value = r.error ?? '没存上。'
    return
  }
  addMsg.value = '记下了。'
  adding.value = { content: '', kind: adding.value.kind, until: '' }
}

function startEdit(m: StoredMemory): void {
  editingId.value = m.id
  editMsg.value = ''
  const until = m.expiresAt ? new Date(m.expiresAt) : null
  const pad = (n: number): string => String(n).padStart(2, '0')
  editing.value = {
    content: m.content,
    kind: m.kind,
    until: until ? `${until.getFullYear()}-${pad(until.getMonth() + 1)}-${pad(until.getDate())}` : ''
  }
}

async function saveEdit(): Promise<void> {
  const r = await api.memoryEdit({
    op: 'update',
    id: editingId.value,
    content: editing.value.content,
    kind: editing.value.kind,
    expiresAt: expiresOf(editing.value)
  })
  if (!r.ok) {
    editMsg.value = r.error ?? '没存上。'
    return
  }
  editingId.value = ''
}

// 经历：较早聊天的摘要、编程任务每一轮做了什么（只在这台电脑的 AI 服务里）
const episodes = ref<MemoryEpisode[]>([])
const confirmingEpisodes = ref(false)

async function loadEpisodes(): Promise<void> {
  episodes.value = await api.memoryEpisodes()
}
onMounted(loadEpisodes)
onActivated(loadEpisodes)

function episodeTag(e: MemoryEpisode): string {
  if (e.source !== 'task') return '聊天'
  const name = e.project.split(/[\\/]/).filter(Boolean).pop()
  return name ? `任务 · ${name}` : '任务'
}

function episodeDate(e: MemoryEpisode): string {
  const d = new Date(e.at)
  return `${d.getMonth() + 1} 月 ${d.getDate()} 日`
}

async function forgetEpisode(id: string): Promise<void> {
  if (await api.memoryForgetEpisode(id)) episodes.value = episodes.value.filter((e) => e.id !== id)
}

async function forgetAllEpisodes(): Promise<void> {
  if (!confirmingEpisodes.value) {
    confirmingEpisodes.value = true
    return
  }
  confirmingEpisodes.value = false
  if (await api.memoryForgetEpisode()) episodes.value = []
}

async function forgetAll(): Promise<void> {
  if (!confirming.value) {
    confirming.value = true // 再点一次才真的全部忘掉
    return
  }
  confirming.value = false
  await api.memoryForgetAll()
}
</script>

<template>
  <h2>她记得的事</h2>
  <p class="lead">
    她从聊天里自己整理出来的，聊天时会挑相关的几条用上。不想让她记得的直接删掉，也可以对她说「忘掉……」。
    登录后在你的几台电脑之间同步，在一台上删掉，别的也会删掉。
  </p>

  <div class="card add">
    <h3>告诉她一件事</h3>
    <div class="row">
      <input
        v-model="adding.content"
        type="text"
        :maxlength="MEMORY_MAX"
        placeholder="比如：我在杭州工作 / 我不喝咖啡 / 周五要交周报"
        aria-label="要她记住的事"
        @keydown.enter="add"
      />
      <select v-model="adding.kind" aria-label="哪一类">
        <option v-for="g in GROUPS" :key="g.kind" :value="g.kind">{{ g.name }}</option>
      </select>
      <input v-if="adding.kind === 'event'" v-model="adding.until" type="date" aria-label="到哪天" title="过了这天就不再提" />
      <button class="btn" :disabled="!adding.content.trim()" @click="add">记下</button>
    </div>
    <p v-if="addMsg" class="hint">{{ addMsg }}</p>
  </div>

  <div v-if="!store.memories.length" class="card empty">
    她还没记住什么。聊天时说到你自己的事（比如「我养了一只猫」），她会慢慢记住；也可以在上面直接告诉她。
  </div>

  <div v-for="g in groups" :key="g.kind" class="card">
    <h3>{{ g.name }} <span class="count">{{ g.items.length }}</span></h3>
    <table class="list">
      <tr v-for="m in g.items" :key="m.id">
        <template v-if="editingId === m.id">
          <td colspan="3">
            <div class="row">
              <input v-model="editing.content" type="text" :maxlength="MEMORY_MAX" aria-label="改成" @keydown.enter="saveEdit" @keydown.esc="editingId = ''" />
              <select v-model="editing.kind" aria-label="哪一类">
                <option v-for="k in GROUPS" :key="k.kind" :value="k.kind">{{ k.name }}</option>
              </select>
              <input v-if="editing.kind === 'event'" v-model="editing.until" type="date" aria-label="到哪天" />
              <button class="btn" @click="saveEdit">保存</button>
              <button class="link" @click="editingId = ''">算了</button>
            </div>
            <p v-if="editMsg" class="hint">{{ editMsg }}</p>
          </td>
        </template>
        <template v-else>
          <td>{{ m.content }}</td>
          <td class="meta">{{ until(m) }}</td>
          <td class="del">
            <button class="x" title="改一下" @click="startEdit(m)">✎</button>
            <button class="x" title="让她忘掉这件事" @click="forget(m.id)">×</button>
          </td>
        </template>
      </tr>
    </table>
  </div>

  <div v-if="store.memories.length" class="actions">
    <button class="btn" :class="{ danger: confirming }" @click="forgetAll">
      {{ confirming ? `真的把 ${store.memories.length} 件事都忘掉吗？再点一次` : '全部忘掉' }}
    </button>
    <button v-if="confirming" class="link" @click="confirming = false">算了</button>
  </div>

  <h3 class="sub">她记下的经历</h3>
  <p class="lead">
    聊得久了，较早的那段她会写成一小段摘要；编程任务每做完一轮，记一句做了什么、改了哪些文件。
    聊天和开新任务时挑相关的用上。只存在这台电脑上，不同步。
  </p>
  <div v-if="!episodes.length" class="card empty">还没有。聊得久了、做过编程任务以后这里会有。</div>
  <div v-else class="card">
    <table class="list">
      <tr v-for="e in episodes" :key="e.id">
        <td class="meta">{{ episodeTag(e) }}</td>
        <td>{{ e.content }}</td>
        <td class="meta">{{ episodeDate(e) }}</td>
        <td class="del"><button class="x" title="删掉这条" @click="forgetEpisode(e.id)">×</button></td>
      </tr>
    </table>
  </div>
  <div v-if="episodes.length" class="actions">
    <button class="btn" :class="{ danger: confirmingEpisodes }" @click="forgetAllEpisodes">
      {{ confirmingEpisodes ? `真的把 ${episodes.length} 条经历都删掉吗？再点一次` : '经历全部删掉' }}
    </button>
    <button v-if="confirmingEpisodes" class="link" @click="confirmingEpisodes = false">算了</button>
  </div>
</template>

<style scoped>
.count {
  margin-left: 4px;
  color: var(--muted);
  font-weight: 400;
}

.meta {
  width: 1%;
  color: var(--muted);
  white-space: nowrap;
}

.del {
  width: 1%;
  white-space: nowrap;
}

.row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}

.row input:not([type='date']) {
  flex: 1 1 260px;
  min-width: 0;
}

.row select {
  flex: 0 0 auto;
  width: auto;
}

.hint {
  margin: 6px 0 0;
  color: var(--muted);
  font-size: 12.5px;
}

.sub {
  margin-top: 28px;
}
</style>
