<script setup lang="ts">
/**
 * 设置 → 对话 →「日历和邮件」：填日历订阅链接、邮箱和授权码，她就能看你的日程和收件箱（只读）。
 * 链接和授权码存在 AI 服务的 .env 里，不回显（和大模型密钥一样）。规则见 aila-ai/app/agenda.py、app/mail.py。
 */
import { t } from '../../i18n'
import { onActivated, onMounted, ref } from 'vue'
import type { AiSettings, AiSettingsUpdate } from '../../../../shared/settings'
import { bridge } from '../../platform/bridge'

const agenda = ref<AiSettings['agenda'] | null>(null)
const calendarUrl = ref('')
const mailUser = ref('')
const mailPass = ref('')
const mailHost = ref('')
const msg = ref<{ ok: boolean; text: string } | null>(null)
const busy = ref(false)

async function load(): Promise<void> {
  if (!bridge) return
  const r = await bridge.aiSettings()
  if (r.ok && r.data?.agenda) {
    agenda.value = r.data.agenda
    mailUser.value = r.data.agenda.mailUser
    mailHost.value = r.data.agenda.mailHost
  }
}

async function save(): Promise<void> {
  if (!bridge) return
  const update: AiSettingsUpdate = { mailUser: mailUser.value.trim(), mailHost: mailHost.value.trim() }
  if (calendarUrl.value.trim()) update.calendarUrl = calendarUrl.value.trim()
  if (mailPass.value.trim()) update.mailPass = mailPass.value.trim()
  busy.value = true
  const r = await bridge.aiSettingsPut(update)
  busy.value = false
  if (!r.ok) {
    msg.value = { ok: false, text: r.error ?? t('出错了。') }
    return
  }
  calendarUrl.value = ''
  mailPass.value = ''
  msg.value = { ok: true, text: t('存好了。') }
  await load()
}

async function clear(what: 'calendar' | 'mail'): Promise<void> {
  if (!bridge) return
  const update: AiSettingsUpdate = what === 'calendar' ? { calendarUrl: '' } : { mailUser: '', mailPass: '', mailHost: '' }
  const r = await bridge.aiSettingsPut(update)
  msg.value = r.ok ? { ok: true, text: t('清掉了。') } : { ok: false, text: r.error ?? t('出错了。') }
  await load()
}

async function test(what: 'calendar' | 'mail'): Promise<void> {
  if (!bridge) return
  busy.value = true
  const r = await bridge.agendaTest(what)
  busy.value = false
  if (!r.ok || !r.data) {
    msg.value = { ok: false, text: r.error ?? t('出错了。') }
    return
  }
  msg.value = {
    ok: true,
    text: what === 'calendar'
      ? t('连上了：接下来 7 天日历上有 {n} 件事。', { n: r.data.count })
      : t('连上了：最近 7 天收件箱 {n} 封，没读的 {u} 封（没有标成已读）。', { n: r.data.count, u: r.data.unread ?? 0 })
  }
}

onMounted(load)
onActivated(load)
</script>

<template>
  <div class="card agenda">
    <h3>{{ t('日历和邮件') }}</h3>
    <p class="hint">{{ t('填好以后她能看你的日程和收件箱：早上说今天有什么会、会前 10 分钟提醒你，问她「邮箱里有什么要回的」也行。只读，不发信、不改日程、不会把信标成已读。') }}</p>

    <div class="row">
      <span class="label">{{ t('日历订阅链接') }}</span>
      <input
        v-model="calendarUrl"
        type="password"
        autocomplete="off"
        :placeholder="agenda?.calendarSet ? t('已设置（不显示）。要换就填新的') : t('webcal:// 或 https:// 开头的 ICS 链接')"
      />
      <button v-if="agenda?.calendarSet" class="btn" :disabled="busy" @click="test('calendar')">{{ t('试试') }}</button>
      <button v-if="agenda?.calendarSet" class="btn danger" :disabled="busy" @click="clear('calendar')">{{ t('清掉') }}</button>
    </div>
    <p class="hint">{{ t('QQ 邮箱 / 163：网页版邮箱打开日历 → 日历设置，找「订阅」「iCal / ICS 链接」，复制那条链接。链接本身就能看你的日程，别发给别人。') }}</p>

    <div class="row">
      <span class="label">{{ t('邮箱') }}</span>
      <input v-model="mailUser" autocomplete="off" placeholder="you@qq.com / you@163.com" />
    </div>
    <div class="row">
      <span class="label">{{ t('授权码') }}</span>
      <input
        v-model="mailPass"
        type="password"
        autocomplete="off"
        :placeholder="agenda?.mailPassSet ? t('已设置（不显示）。要换就填新的') : t('不是登录密码')"
      />
      <button v-if="agenda?.mailPassSet" class="btn" :disabled="busy" @click="test('mail')">{{ t('试试') }}</button>
      <button v-if="agenda?.mailPassSet" class="btn danger" :disabled="busy" @click="clear('mail')">{{ t('清掉') }}</button>
    </div>
    <p class="hint">{{ t('授权码在网页版邮箱：设置 → 账户（QQ 邮箱）或 设置 → POP3/SMTP/IMAP（163），打开 IMAP 服务后生成。QQ、163、126、foxmail 自动认服务器；别的邮箱在下面填 IMAP 服务器。') }}</p>
    <div class="row">
      <span class="label">{{ t('IMAP 服务器（可不填）') }}</span>
      <input v-model="mailHost" autocomplete="off" placeholder="imap.example.com" />
    </div>

    <div class="actions">
      <button class="btn primary" :disabled="busy" @click="save">{{ t('保存') }}</button>
      <span v-if="msg" class="msg" :class="msg.ok ? 'ok' : 'error'">{{ msg.text }}</span>
    </div>
  </div>
</template>
