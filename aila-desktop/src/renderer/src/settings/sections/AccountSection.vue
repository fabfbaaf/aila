<script setup lang="ts">
import { computed, onActivated, ref } from 'vue'
import { DEFAULT_SERVER_URL, type AccountStatus, type AuthForm } from '../../../../shared/cloud'
import { bridge } from '../../platform/bridge'
import { store } from '../store'

const api = bridge!
const mode = ref<AuthForm['mode']>('login')
const email = ref('')
const password = ref('')
const displayName = ref('')
const serverUrl = ref(DEFAULT_SERVER_URL)
const busy = ref(false)
const error = ref('')
const details = ref<AccountStatus | null>(null)
const confirmLogout = ref(false)

const signedIn = computed(() => store.account != null && store.account.status !== 'signed-out')

const STATUS: Record<string, string> = {
  online: '已同步',
  connecting: '正在连接……',
  offline: '连不上服务器，会自动重试'
}

async function submit(): Promise<void> {
  if (busy.value) return
  error.value = ''
  if (!email.value.trim() || !password.value) {
    error.value = '请填写邮箱和密码'
    return
  }
  busy.value = true
  const res = await api.accountAuth({
    mode: mode.value,
    email: email.value.trim(),
    password: password.value,
    displayName: displayName.value.trim() || undefined,
    serverUrl: serverUrl.value
  })
  busy.value = false
  password.value = '' // 密码不留在界面里
  if (res.ok) await loadDetails()
  else error.value = res.error ?? '没有成功'
}

async function logout(): Promise<void> {
  if (!confirmLogout.value) {
    confirmLogout.value = true
    return
  }
  confirmLogout.value = false
  busy.value = true
  await api.accountLogout()
  busy.value = false
  details.value = null
}

async function loadDetails(): Promise<void> {
  details.value = signedIn.value ? await api.accountDetails() : null
  if (store.account) serverUrl.value = store.account.serverUrl
}

function since(iso: string): string {
  const d = new Date(iso)
  return `${d.getMonth() + 1} 月 ${d.getDate()} 日 ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

onActivated(loadDetails)
</script>

<template>
  <h2>账号与同步</h2>
  <p class="lead">登录后，聊天记录、她记得的事和部分设置会在你的几台电脑之间同步。不登录也能用，只是都留在这台电脑上。</p>

  <template v-if="signedIn && store.account">
    <div class="card">
      <h3>{{ store.account.displayName }}</h3>
      <div class="row">
        <span class="label">邮箱</span>
        <span>{{ store.account.email }}</span>
      </div>
      <div class="row">
        <span class="label">同步状态</span>
        <span>
          <span class="tag" :class="store.account.status === 'online' ? 'good' : 'warn'">{{ STATUS[store.account.status] }}</span>
          <span v-if="store.account.pending" class="hint" style="display: inline">还有 {{ store.account.pending }} 条没传上去</span>
        </span>
      </div>
      <div class="row">
        <span class="label">服务器</span>
        <span class="hint" style="margin: 0">{{ store.account.serverUrl }}</span>
      </div>
    </div>

    <div v-if="details?.devices?.length" class="card">
      <h3>登录着的电脑</h3>
      <table class="list">
        <tr v-for="d in details.devices" :key="d.deviceId">
          <td>{{ d.deviceName }}<span v-if="d.deviceId === store.account.currentDeviceId" class="tag good" style="margin-left: 6px">这台</span></td>
          <td class="hint">最近活动 {{ since(d.lastActiveAt) }}</td>
        </tr>
      </table>
    </div>

    <div class="actions">
      <button class="btn" :class="{ danger: confirmLogout }" :disabled="busy" @click="logout">
        {{ confirmLogout ? '退出后这台电脑上的聊天记录和记忆会清空（云端的还在），确定吗？' : '退出登录' }}
      </button>
      <button v-if="confirmLogout" class="link" @click="confirmLogout = false">算了</button>
    </div>
  </template>

  <div v-else class="card">
    <div class="tabs">
      <button :class="{ on: mode === 'login' }" @click="mode = 'login'">登录</button>
      <button :class="{ on: mode === 'register' }" @click="mode = 'register'">注册</button>
    </div>
    <div class="row">
      <span class="label">邮箱</span>
      <input v-model="email" type="email" autocomplete="username" spellcheck="false" @keydown.enter="submit" />
    </div>
    <div class="row">
      <span class="label">密码</span>
      <input
        v-model="password"
        type="password"
        :placeholder="mode === 'register' ? '至少 8 位' : ''"
        :autocomplete="mode === 'register' ? 'new-password' : 'current-password'"
        @keydown.enter="submit"
      />
    </div>
    <div v-if="mode === 'register'" class="row">
      <span class="label">她怎么称呼你</span>
      <input v-model="displayName" type="text" maxlength="50" placeholder="可以不填" />
    </div>
    <div class="row">
      <span class="label">同步服务器</span>
      <div>
        <input v-model="serverUrl" type="text" spellcheck="false" />
        <span class="hint">自己部署的 aila-server 地址。本机调试：在 aila-server 目录运行 mvnw spring-boot:test-run</span>
      </div>
    </div>
    <p v-if="error" class="msg error">{{ error }}</p>
    <div class="actions">
      <button class="btn primary" :disabled="busy" @click="submit">
        {{ busy ? '请稍等…' : mode === 'login' ? '登录' : '注册并登录' }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.tabs {
  display: flex;
  gap: 6px;
  margin-bottom: 8px;
}

.tabs button {
  padding: 5px 18px;
  border: none;
  border-radius: 8px;
  background: #eef0f6;
  color: #6b7188;
  font: inherit;
  cursor: pointer;
}

.tabs button.on {
  background: var(--accent);
  color: #fff;
}
</style>
