<script setup lang="ts">
import { computed, ref } from 'vue'
import type { MemoryKind, StoredMemory } from '../../../../shared/cloud'
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

  <div v-if="!store.memories.length" class="card empty">
    她还没记住什么。聊天时说到你自己的事（比如「我养了一只猫」），她会慢慢记住。
  </div>

  <div v-for="g in groups" :key="g.kind" class="card">
    <h3>{{ g.name }} <span class="count">{{ g.items.length }}</span></h3>
    <table class="list">
      <tr v-for="m in g.items" :key="m.id">
        <td>{{ m.content }}</td>
        <td class="meta">{{ until(m) }}</td>
        <td class="del"><button class="x" title="让她忘掉这件事" @click="forget(m.id)">×</button></td>
      </tr>
    </table>
  </div>

  <div v-if="store.memories.length" class="actions">
    <button class="btn" :class="{ danger: confirming }" @click="forgetAll">
      {{ confirming ? `真的把 ${store.memories.length} 件事都忘掉吗？再点一次` : '全部忘掉' }}
    </button>
    <button v-if="confirming" class="link" @click="confirming = false">算了</button>
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
}
</style>
