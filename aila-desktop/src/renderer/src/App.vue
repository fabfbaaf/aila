<script setup lang="ts">
import { t } from './i18n'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Vector3 } from 'three'
import SpeechBubble from './components/SpeechBubble.vue'
import ChatInput from './components/ChatInput.vue'
import type { BubbleAsk } from './components/bubble'
import type { CharacterEvent } from '../../shared/settings'
import { BUILTIN_GESTURES, type ConfirmAsk } from '../../shared/chat'
import { cleanMotions } from '../../shared/extensions'
import type { AgentProgress } from '../../shared/agent'
import { pendingFixOf, type LocatedError } from '../../shared/workbench'
import { confirmIntent } from './chat/confirmIntent'
import { parseMove, shouldStroll } from '../../shared/move'
import { INTRO_LINES } from '../../shared/intro'
import { ScreenWatch, helpRequest, type ScreenFinding } from './screen/ScreenWatch'
import { Behavior } from './behavior/Behavior'
import { Conversation, type Voice } from './chat/Conversation'
import { createTransport } from './chat/transport'
import { Stage, type HitRegion } from './scene/Stage'
import { PlaceholderAvatar } from './avatar/PlaceholderAvatar'
import { loadVrmAvatar } from './avatar/VrmAvatar'
import { Live2DUnavailable, loadLive2DAvatar } from './avatar/Live2DAvatar'
import type { LoadedModel } from '../../shared/model'
import { applyExtensionLines, lines, pick } from './persona/lines'
import { bridge } from './platform/bridge'
import { Speaker } from './voice/Speaker'
import { VoiceController } from './voice/VoiceController'
import { VoiceLink } from './voice/VoiceLink'
import { MIC_SAMPLE_RATE } from './voice/Microphone'
import type { VoiceMenuAction, VoiceMenuState } from '../../shared/voice'
import type { DesktopStatus, Reminder } from '../../shared/desktop'
import { targetFps } from './scene/frameBudget'
import { clipboardMessage, fileMessage, fileTask, mentionsClipboard, type DropInfo } from '../../shared/drop'

const stageEl = ref<HTMLDivElement>()
/** 出场（shared/entrance.ts）：地址带 #enter 时先不露面，主进程说冒出来再出来；各段的缩放动画在下面的样式里 */
const entrancePhase = ref<'' | 'waiting' | 'emerge' | 'crouch' | 'jump' | 'land'>(location.hash === '#enter' ? 'waiting' : '')
const fileInput = ref<HTMLInputElement>()
const chatInput = ref<InstanceType<typeof ChatInput>>()
const bubbleText = ref('')
const bubbleVisible = ref(false)
const bubbleFullText = ref('')
const bubbleExpanded = ref(false)
const overAvatar = ref(false)
const chatOpen = ref(false)
const avatarTop = ref('50%')
/** 她在等用户确认的操作（气泡里显示「好 / 不要」） */
const convAsk = ref<ConfirmAsk | null>(null)
/** 屏幕上看到的报错，在问用户要不要帮忙 */
const screenAsk = ref<ScreenFinding | null>(null)
/** 那个报错落在登记的哪个项目里（落在了才给「在星图里看」） */
const screenLocated = ref<LocatedError | null>(null)
/** 正在看屏幕（脚边显示一个标识） */
const watching = ref(false)
/** 到点的提醒（气泡里「知道了 / 10 分钟后再提醒」） */
const reminderAsk = ref<{ reminder: Reminder; late: boolean } | null>(null)
/** 拖到她身上的文件：「讲讲它 / 交给任务页 / 算了」 */
const dropAsk = ref<DropInfo | null>(null)
/** 每天见面、日报：说完气泡里给「好 / 看看任务」（有具体的事可说时） */
const briefingAsk = ref<{ text: string; detail: boolean } | null>(null)
/** 她自己做好了一件作品：气泡里给「看看 / 待会儿」 */
const workAsk = ref<{ id: string; title: string } | null>(null)
/** 她在做自己的作品（编程任务也会让她「敲键盘」：两边都结束才放下） */
let makingWork = false
/** 编程任务在做 */
let agentWorking = false

function stopAgentWork(): void {
  agentWorking = false
  behavior.setWorking(makingWork) // 她自己的作品还在做的话接着敲
}
/** 任务页里有个操作等你同意：气泡里给「去看看」，点了打开那个任务（同意还是在任务页里点，那里看得到完整的改动） */
const agentAsk = ref<{ sid: string; action: string } | null>(null)
/** 开场演出里问「借一下你的鼠标？」（气泡里给「借给你 / 不借」，回答交给主进程的导演） */
const introAsk = ref(false)
/** 桌面状态：藏没藏、能不能说话、省不省电、拔没拔电（主进程推过来） */
const desk = ref<DesktopStatus | null>(null)

const bubbleAsk = computed<BubbleAsk | null>(() => {
  if (introAsk.value) {
    return {
      tag: t(INTRO_LINES.mouseTag),
      detail: t(INTRO_LINES.mouseDetail),
      buttons: [
        { label: t(INTRO_LINES.lend), value: 'lend', primary: true },
        { label: t(INTRO_LINES.keep), value: 'keep' }
      ]
    }
  }
  if (dropAsk.value && !convAsk.value) {
    const d = dropAsk.value
    return {
      tag: d.kind === 'dir' ? t('文件夹') : t('文件'),
      detail: d.rel ?? d.name,
      buttons: [
        ...(d.kind === 'text' ? [{ label: t('讲讲它'), value: 'chat', primary: true }] : []),
        ...(d.project ? [{ label: t('交给任务页'), value: 'task', primary: d.kind === 'dir' }] : []),
        { label: t('算了'), value: 'no' }
      ]
    }
  }
  if (workAsk.value && !convAsk.value && !reminderAsk.value && !screenAsk.value) {
    return {
      tag: t('作品'),
      detail: workAsk.value.title,
      buttons: [
        { label: t('看看'), value: 'see', primary: true },
        { label: t('待会儿'), value: 'later' }
      ]
    }
  }
  if (briefingAsk.value && !convAsk.value && !reminderAsk.value && !screenAsk.value) {
    return {
      tag: t('今天'),
      detail: '',
      buttons: briefingAsk.value.detail
        ? [
            { label: t('好'), value: 'ok', primary: true },
            { label: t('看看任务'), value: 'tasks' }
          ]
        : [{ label: t('好'), value: 'ok', primary: true }]
    }
  }
  if (agentAsk.value && !convAsk.value && !reminderAsk.value && !screenAsk.value) {
    return {
      tag: t('任务'),
      detail: agentAsk.value.action,
      buttons: [
        { label: t('去看看'), value: 'open', primary: true },
        { label: t('等会儿'), value: 'later' }
      ]
    }
  }
  if (reminderAsk.value && !convAsk.value) {
    return {
      tag: t('提醒'),
      detail: reminderAsk.value.reminder.text,
      buttons: [
        { label: t('知道了'), value: 'ok', primary: true },
        { label: t('10 分钟后再提醒'), value: 'snooze' }
      ]
    }
  }
  if (convAsk.value) {
    const a = convAsk.value
    return {
      tag: a.skill,
      detail: a.detail,
      buttons: [
        { label: t('好'), value: 'yes', primary: true },
        { label: t('不要'), value: 'no' }
      ]
    }
  }
  if (screenAsk.value) {
    const f = screenAsk.value
    return {
      tag: f.app,
      detail: f.summary,
      note: t('点「好」才把这 {n} 行报错发给大模型（已打码）', { n: f.snippet.split('\n').length }),
      buttons: [
        { label: t('好'), value: 'yes', primary: true },
        ...(screenLocated.value
          ? [
              { label: t('交给她修'), value: 'task' },
              { label: t('在星图里看'), value: 'map' }
            ]
          : []),
        { label: t('不用'), value: 'no' },
        { label: t('一小时内别提醒'), value: 'mute' }
      ]
    }
  }
  return null
})

const behavior = new Behavior()
const transport = createTransport()
const speaker = new Speaker()
/** 她的「嗓子」：合成走 AI 服务，播放和口型在本地 */
const voice: Voice = {
  get enabled() {
    return voiceControl.speakEnabled
  },
  synthesize: async (text) => {
    const wav = await transport.synthesize(text)
    return wav ? speaker.decode(wav) : null
  },
  play: (buffer) => speaker.play(buffer),
  stop: () => speaker.stop(),
  get playing() {
    return speaker.playing
  },
  mouth: () => speaker.mouth()
}
const conversation = new Conversation(behavior, transport, voice)
const voiceControl = new VoiceController(behavior, conversation, speaker, new VoiceLink(transport.voiceUrl))
voiceControl.warm = (parts) => transport.warmup(parts)
const screenWatch = new ScreenWatch(transport.screenUrl)
let stage: Stage | null = null
let usingPlaceholder = true
let bubbleTimer = 0
const lookTarget = new Vector3()

/** holdMs 为 null 时一直显示，直到下一次更新；在问用户报错的事时也一直显示，等回答 */
function showBubble(text: string, holdMs: number | null, fullText = ''): void {
  bubbleText.value = text
  bubbleFullText.value = fullText
  if (!fullText) bubbleExpanded.value = false
  bubbleVisible.value = true
  clearTimeout(bubbleTimer)
  if (holdMs !== null && !bubbleExpanded.value && !screenAsk.value && !reminderAsk.value && !briefingAsk.value && !dropAsk.value) {
    bubbleTimer = window.setTimeout(() => (bubbleVisible.value = false), holdMs)
  }
}

function toggleBubbleFullText(): void {
  bubbleExpanded.value = !bubbleExpanded.value
  showBubble(bubbleText.value, conversation.busy ? null : 10_000, bubbleFullText.value)
}

behavior.onSay = showBubble
conversation.onBubble = showBubble
voiceControl.onBubble = showBubble
conversation.onAsk = (a) => (convAsk.value = a)
// 等用户点头的是代码修改：告诉星图（星图里会显示这份修改，双手合十可以确认）
watch(convAsk, (a) => bridge?.reportPendingFix?.(pendingFixOf(a)), { immediate: true })

function onVoiceMenu(action: VoiceMenuAction): void {
  if (action === 'toggle-wake') void voiceControl.toggleWake()
  else if (action === 'toggle-speak') voiceControl.toggleSpeak()
  else if (action === 'toggle-watch') toggleWatch()
  else void voiceControl.listenNow()
}

/** 右键菜单、设置窗口里显示的开关 */
function switches(): VoiceMenuState {
  return { ...voiceControl.menuState(), watch: screenWatch.on }
}

function reportSwitches(): void {
  bridge?.voiceReport(switches())
}

// ---------- 留意屏幕上的报错 ----------

/** 多久没回答就收起（不算拒绝） */
const SCREEN_ASK_MS = 45_000
let screenAskTimer = 0
/** 她正忙（在回复、在等别的确认）时看到的报错：忙完再问 */
let pendingFinding: ScreenFinding | null = null

function toggleWatch(): void {
  screenWatch.setEnabled(!screenWatch.on)
  conversation.announce(screenWatch.on ? lines.watchOn : lines.watchOff)
}

screenWatch.onState = (s) => {
  watching.value = s.watching
  if (!s.on) clearScreenAsk()
  if (s.error) {
    behavior.react('sad')
    behavior.say(lines.watchUnavailable(s.error), 6000)
  }
  reportSwitches()
}

screenWatch.onFinding = (f) => {
  // 忙着、或者现在不方便说话（别人在全屏、在演示、免打扰）：先记着，之后再问
  if (conversation.busy || convAsk.value || screenAsk.value || reminderAsk.value || desk.value?.presence.quiet) pendingFinding = f
  else askAboutFinding(f)
}

function askAboutFinding(f: ScreenFinding): void {
  pendingFinding = null
  screenAsk.value = f
  screenLocated.value = null
  // 后台查一下报错落在不在登记的项目里：在的话多一个「在星图里看」
  void bridge?.codeLocateAll(f.snippet).then((r) => {
    // 按 id 比：放进 ref 的对象会被包成响应式代理，和原来的对象不相等
    if (screenAsk.value?.id === f.id && r.ok && r.data) screenLocated.value = r.data
  })
  behavior.gesture('tilt')
  conversation.announce(lines.watchAsk(f.app))
  clearTimeout(screenAskTimer)
  screenAskTimer = window.setTimeout(() => {
    clearScreenAsk()
    bubbleVisible.value = false
  }, SCREEN_ASK_MS)
}

function clearScreenAsk(): void {
  clearTimeout(screenAskTimer)
  screenAsk.value = null
  screenLocated.value = null
}

function answerScreen(value: string): void {
  const f = screenAsk.value
  if (!f) return
  if (value === 'map') {
    // 打开星图标出那颗红色星球；她的问题还留着，看完可以再点「好」
    bridge?.openWorkbench({ project: screenLocated.value?.project, error: f.snippet })
    return
  }
  const located = screenLocated.value
  clearScreenAsk()
  if (value === 'yes') {
    conversation.send(helpRequest(f))
  } else if (value === 'task' && located) {
    // 交给工作窗口里的编程 Agent：在报错所在的项目里开个任务（改文件之前她还会问你）
    void startFix(located.project, f.snippet)
  } else if (value === 'mute') {
    screenWatch.pause(3600)
    conversation.announce(lines.watchMuted)
  } else {
    screenWatch.declined()
    conversation.announce(lines.watchDeclined)
  }
}

async function startFix(project: string, snippet: string): Promise<void> {
  if (!bridge) return
  behavior.gesture('nod')
  const r = await bridge.agentTask({ project, text: lines.fixTask(snippet) })
  if (r.ok) behavior.say(pick(lines.agentTaskStarted), 3000)
  else conversation.announce(lines.agentTaskFailed(r.error ?? ''))
}

function onAnswer(value: string): void {
  if (introAsk.value) {
    introAsk.value = false
    bubbleVisible.value = false
    bridge?.introAnswer(value === 'lend' ? 'lend' : 'keep')
    return
  }
  if (dropAsk.value && !convAsk.value) void answerDrop(value)
  else if (workAsk.value && !convAsk.value && !reminderAsk.value && !screenAsk.value) answerWork(value)
  else if (briefingAsk.value && !convAsk.value && !reminderAsk.value && !screenAsk.value) answerBriefing(value)
  else if (agentAsk.value && !convAsk.value && !reminderAsk.value && !screenAsk.value) answerAgent(value)
  else if (reminderAsk.value && !convAsk.value) answerReminder(value)
  else if (convAsk.value) conversation.answer(value === 'yes')
  else answerScreen(value)
}

// ---------- 提醒、久坐 ----------

/** 到点的提醒多久没回答就收起（不算没看到：主进程那边已经响过了） */
const REMINDER_ASK_MS = 120_000
let reminderTimer = 0
/** 她正在问你别的事（确认、报错）时到点的提醒：问完再说 */
const pendingReminders: { reminder: Reminder; late: boolean }[] = []

function showReminder(r: { reminder: Reminder; late: boolean }): void {
  if (convAsk.value || screenAsk.value || reminderAsk.value) {
    pendingReminders.push(r)
    return
  }
  reminderAsk.value = r
  behavior.react('happy')
  behavior.gesture('tilt')
  const lateAt = r.late ? new Date(r.reminder.at).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) : null
  conversation.announce(lines.reminder(r.reminder.text, lateAt))
  clearTimeout(reminderTimer)
  reminderTimer = window.setTimeout(() => {
    reminderAsk.value = null
    bubbleVisible.value = false
  }, REMINDER_ASK_MS)
}

function answerReminder(value: string): void {
  const r = reminderAsk.value
  if (!r) return
  clearTimeout(reminderTimer)
  reminderAsk.value = null
  if (value === 'snooze') {
    void bridge?.reminderSnooze(r.reminder.text, 10)
    behavior.gesture('nod')
    behavior.say(lines.reminderSnoozed, 2500)
  } else {
    behavior.gesture('nod')
    bubbleVisible.value = false
  }
}

// ---------- 拖给她的文件、剪贴板 ----------

function onDragOver(e: DragEvent): void {
  if (e.dataTransfer?.types.includes('Files')) {
    e.preventDefault()
    e.dataTransfer.dropEffect = 'copy'
  }
}

async function onDrop(e: DragEvent): Promise<void> {
  const files = [...(e.dataTransfer?.files ?? [])]
  if (!bridge || !files.length) return
  lastInteraction = performance.now()
  const paths = files.map((f) => bridge!.pathForFile(f)).filter(Boolean)
  const [first] = await bridge.dropInspect(paths)
  if (!first) return
  behavior.gesture('tilt')
  if (files.length > 1) behavior.say(lines.dropMany(files.length), 1800)
  const reasons: Partial<Record<DropInfo['kind'], string>> = {
    secret: lines.dropSecret,
    binary: lines.dropBinary,
    'too-big': lines.dropTooBig,
    missing: lines.dropMissing
  }
  const why = reasons[first.kind]
  if (why) {
    behavior.react('sad')
    behavior.say(why, 3500)
    return
  }
  dropAsk.value = first
  conversation.announce(first.kind === 'dir' ? lines.dropDirAsk(first.name) : lines.dropAsk(first.name))
}

async function answerDrop(value: string): Promise<void> {
  const d = dropAsk.value
  dropAsk.value = null
  if (!d || !bridge) return
  if (value === 'chat') {
    const content = await bridge.dropRead(d.path)
    if (content === null) {
      behavior.say(lines.dropMissing, 2500)
      return
    }
    conversation.send(fileMessage(d.name, content))
  } else if (value === 'task' && d.project) {
    behavior.gesture('nod')
    const r = await bridge.agentTask({ cwd: d.project, text: fileTask(d.rel ?? d.name) })
    if (r.ok) behavior.say(pick(lines.agentTaskStarted), 3000)
    else conversation.announce(lines.agentTaskFailed(r.error ?? ''))
  } else {
    bubbleVisible.value = false
  }
}

/** 说的话提到剪贴板：把剪贴板里的文字（打码后）带上再发 */
async function sendWithClipboard(text: string): Promise<void> {
  if (!bridge) return conversation.send(text)
  behavior.say(lines.clipboardReading, 1500)
  const msg = clipboardMessage(text, await bridge.clipboardText())
  if (msg) conversation.send(msg)
  else behavior.say(lines.clipboardEmpty, 3500)
}

// ---------- 每天见面、日报 ----------

const BRIEFING_ASK_MS = 60_000
let briefingTimer = 0

function showBriefing(b: { text: string; detail: boolean }): void {
  // 正在聊、在问别的事：这次先不说（主进程已经记成说过了，免得一会儿又冒出来打断你）
  if (conversation.chatting || convAsk.value || screenAsk.value || reminderAsk.value) return
  briefingAsk.value = b
  behavior.gesture('wave')
  conversation.announce(b.text)
  clearTimeout(briefingTimer)
  briefingTimer = window.setTimeout(() => {
    briefingAsk.value = null
    bubbleVisible.value = false
  }, BRIEFING_ASK_MS)
}

function answerAgent(value: string): void {
  const ask = agentAsk.value
  agentAsk.value = null
  bubbleVisible.value = false
  if (ask && value === 'open') bridge?.openAgent(ask.sid)
}

/** 她做好了作品：说一句，等你点「看看」（一分钟没理就收起，作品页里一直都在） */
function showWork(w: { id: string; title: string; why: string }): void {
  if (conversation.chatting || convAsk.value || screenAsk.value || reminderAsk.value) return
  workAsk.value = { id: w.id, title: w.title }
  behavior.react('happy')
  behavior.gesture('wave')
  conversation.announce(lines.workDone(w.title, w.why))
  clearTimeout(briefingTimer)
  briefingTimer = window.setTimeout(() => {
    workAsk.value = null
    bubbleVisible.value = false
  }, BRIEFING_ASK_MS)
}

function answerWork(value: string): void {
  clearTimeout(briefingTimer)
  const ask = workAsk.value
  workAsk.value = null
  behavior.gesture('nod')
  if (ask && value === 'see') {
    bridge?.studioShow(ask.id)
    behavior.say(lines.workOpen, 2000)
  } else {
    bubbleVisible.value = false
  }
}

function answerBriefing(value: string): void {
  clearTimeout(briefingTimer)
  briefingAsk.value = null
  behavior.gesture('nod')
  if (value === 'tasks') {
    bridge?.openAgent()
    behavior.say(lines.briefingOpen, 2000)
  } else {
    bubbleVisible.value = false
  }
}

// ---------- 藏起来、省电 ----------

/** 最近一次和她互动（点、拖、打字）的时间，和鼠标最近一次动的时间：省电时决定帧率 */
let lastInteraction = performance.now()
let lastCursorMove = performance.now()
const INTERACTION_GRACE_MS = 3000

function applyDesktop(s: DesktopStatus | null): void {
  desk.value = s
  // 藏起来了：听到唤醒词也不应声
  voiceControl.muted = !!s && !s.presence.visible
}

function frameLimit(): number | null {
  const s = desk.value
  if (!s) return null
  const now = performance.now()
  const active =
    chatOpen.value ||
    bubbleVisible.value ||
    conversation.busy ||
    speaker.playing ||
    press !== null ||
    now - lastInteraction < INTERACTION_GRACE_MS
  return targetFps({ saver: s.prefs.powerSaver, onBattery: s.onBattery, active, sinceMove: now - lastCursorMove })
}

/** 调试：用她自己的合成声音模拟用户说一句话，整条语音链路照常走一遍（不需要麦克风） */
async function hear(text: string): Promise<void> {
  const wav = await transport.synthesize(text)
  if (!wav) return
  const audio = await new OfflineAudioContext(1, 1, MIC_SAMPLE_RATE).decodeAudioData(wav) // 顺便重采样到 16kHz
  await voiceControl.simulate(audio.getChannelData(0))
}

// ---------- 对话框 ----------

async function openChat(): Promise<void> {
  lastInteraction = performance.now()
  if (!chatOpen.value) {
    chatOpen.value = true
    if (!conversation.busy) behavior.listen()
  }
  bridge?.focusWindow() // 窗口平时不抢焦点，打字前要拿到
  await nextTick()
  chatInput.value?.focus()
}

function closeChat(): void {
  chatOpen.value = false
  bridge?.blurWindow() // 把焦点还给之前在用的程序
  bridge?.chatClosed()
}

function onSend(text: string): void {
  // 她在等确认时打「好」「不要」就是回答；打别的话当新话题（这个操作不执行）
  if (conversation.answerText(text)) return
  if (screenAsk.value) {
    const intent = confirmIntent(text)
    if (intent) {
      answerScreen(intent === 'yes' ? 'yes' : 'no')
      return
    }
    clearScreenAsk() // 说起别的事了：这次就不问了
  }
  walkIfAsked(text)
  if (mentionsClipboard(text)) void sendWithClipboard(text)
  else conversation.send(text)
}

/** 整句就是「去右上角」「往左一点」：当场就走，不等她回话（这句话照样发给她，她会接一句） */
function walkIfAsked(text: string): void {
  const target = parseMove(text)
  if (target) bridge?.moveTo(target)
}

// ---------- 设置窗口里做的事，她在桌面上有点反应 ----------

function onCharacterEvent(ev: CharacterEvent): void {
  switch (ev.kind) {
    case 'forget-one':
      behavior.say(pick(lines.forgetOne), 2200)
      break
    case 'forget-all':
      behavior.react('sad')
      behavior.say(lines.forgetAll, 3500)
      break
    case 'knowledge-added':
      behavior.react('happy')
      behavior.say(ev.names.length === 1 ? t('……《{name}》我读完了。', { name: ev.names[0] }) : t('……{n} 份资料我都读完了。', { n: ev.names.length }), 3500)
      break
    case 'settings-saved':
      behavior.gesture('nod')
      break
    case 'agent':
      onAgentProgress(ev.progress)
      break
    case 'reminder':
      showReminder({ reminder: ev.reminder, late: ev.late })
      break
    case 'reminder-failed':
      behavior.react('sad')
      behavior.say(lines.reminderFailed(ev.why), 4000)
      break
    case 'briefing':
      showBriefing({ text: ev.text, detail: ev.detail })
      break
    case 'work':
      showWork(ev.work)
      break
    case 'work-making':
      // 在做自己的作品：桌面上「敲键盘」，偶尔小声说一句；做完放下（编程任务还在做的话由它来放）
      makingWork = ev.on
      behavior.setWorking(ev.on || agentWorking)
      if (ev.on && !conversation.chatting && !desk.value?.presence.quiet) behavior.say(pick(lines.workMaking), 2600)
      break
    case 'review':
      if (!conversation.chatting && !convAsk.value && !screenAsk.value) {
        behavior.gesture('nod')
        conversation.announce(pick(lines.weeklyReview))
      }
      break
    case 'late-night':
      if (!conversation.chatting && !convAsk.value && !screenAsk.value) {
        behavior.gesture('stretch')
        conversation.announce(pick(lines.lateNight))
      }
      break
    case 'sit-break':
      if (!conversation.chatting && !convAsk.value && !screenAsk.value) {
        behavior.gesture('stretch') // 她先伸个懒腰
        conversation.announce(pick(lines.sitBreak(ev.minutes)))
      }
      break
  }
}

/** 工作窗口里的编程任务有进展：做个动作，说一句。她正在和你说话或者等你回答时不插嘴（工作窗口里都看得到） */
function onAgentProgress(p: AgentProgress): void {
  // 只在真的和你对话、等你回答时不插嘴；她自己念的上一句台词（「要改……可以吗」）可以被新的进展盖掉
  const quiet = conversation.chatting || !!convAsk.value || !!screenAsk.value || !!desk.value?.presence.quiet
  switch (p.kind) {
    case 'start':
      behavior.gesture('nod')
      agentWorking = true
      behavior.setWorking(true) // 在桌面上「敲键盘」，做完放下
      if (!quiet) behavior.say(pick(lines.agentStart), 2200)
      break
    case 'confirm':
      behavior.gesture('tilt')
      if (!quiet) conversation.announce(lines.agentConfirm(p.action))
      if (p.sid) agentAsk.value = { sid: p.sid, action: p.action }
      break
    case 'done':
      agentAsk.value = null
      stopAgentWork()
      behavior.react('happy')
      if (!quiet) conversation.announce(lines.agentDone(p.files))
      break
    case 'stopped':
      stopAgentWork()
      if (!quiet && lines.agentStopped[p.reason]) behavior.say(lines.agentStopped[p.reason], 3500)
      break
    case 'error':
      stopAgentWork()
      behavior.react('sad')
      if (!quiet) behavior.say(lines.agentError, 3500)
      break
    case 'tests':
      // 小情绪：过了就高兴（之前一直没过的话说一句），连着没过三次叹口气、打打气（之后每多三次再说一次）
      if (p.ok) {
        behavior.react('happy')
        if (p.recovered) {
          behavior.gesture('nod')
          if (!quiet) behavior.say(pick(lines.testsRecovered), 2600)
        }
      } else if (p.streak >= 3 && p.streak % 3 === 0) {
        behavior.react('sad')
        behavior.gesture('think')
        if (!quiet) behavior.say(pick(lines.testsStruggling), 3500)
      }
      break
    case 'budget':
      stopAgentWork()
      behavior.gesture('tilt')
      if (!quiet) behavior.say(lines.agentBudget, 5000)
      break
    case 'stage':
      behavior.gesture('nod')
      if (!quiet) behavior.say(lines.agentStage(p.title), 3000)
      break
    case 'plan_end':
      stopAgentWork()
      behavior.react('happy')
      if (!quiet) conversation.announce(lines.agentPlanEnd)
      break
    case 'plan_pause':
      stopAgentWork()
      behavior.gesture('tilt')
      if (!quiet) behavior.say(lines.agentPlanPause(p.text), 5000)
      break
  }
}

/** 对话记录：启动时从本地恢复；说出口的话交给主进程保存、同步；别的设备的话合并进来 */
async function connectHistory(): Promise<void> {
  if (!bridge) return // 浏览器调试时只在内存里
  const api = bridge
  conversation.restore(await api.historyLoad())
  conversation.onCommit = (m) => api.historyAppend(m)
  subscriptions.push(
    api.onHistoryRemote((changes) => {
      conversation.applyRemote(changes)
      for (const s of changes.settings) voiceControl.applySetting(s.key, s.value, s.deleted)
    })
  )
  voiceControl.onSettingChanged = (key, value) => api.settingPut(key, value)
  // 语音和看屏幕的开关存在这里，设置窗口通过主进程读写
  voiceControl.onPrefsChanged = reportSwitches
  reportSwitches()
  subscriptions.push(api.onCharacterEvent(onCharacterEvent))
}

/** 订阅了主进程的哪些消息：组件卸载时（开发时热更新）全部退订，旧组件不再响应 */
const subscriptions: (() => void)[] = []

// ---------- 模型 ----------

/** quiet：启动时加载上次的模型，不用说「换好了」。VRM 和 Live2D 都实现同一个 Avatar 接口 */
async function useModel(model: LoadedModel, quiet = false): Promise<void> {
  try {
    const avatar = model.kind === 'live2d' ? await loadLive2DAvatar(model.model) : await loadVrmAvatar(model.data)
    stage?.setAvatar(avatar)
    usingPlaceholder = false
    if (quiet) return
    behavior.react('happy')
    behavior.say(lines.modelLoaded)
  } catch (err) {
    console.error(`加载 ${model.kind === 'live2d' ? 'Live2D' : 'VRM'} 模型失败`, err)
    behavior.react('sad')
    // Live2D 运行库没装：直接说怎么装
    behavior.say(err instanceof Live2DUnavailable ? `……${err.message}。` : lines.modelFailed)
  }
}

/** 自带的原创形象（public/avatar/aila.vrm）。用 XHR 读：打包后页面是 file:// 地址，fetch 读不了本地文件 */
async function loadBuiltin(announce = false): Promise<void> {
  const data = await new Promise<ArrayBuffer | null>((resolve) => {
    const xhr = new XMLHttpRequest()
    xhr.open('GET', new URL('./avatar/aila.vrm', location.href).href)
    xhr.responseType = 'arraybuffer'
    xhr.onload = () => resolve(xhr.status === 200 || (xhr.status === 0 && xhr.response) ? (xhr.response as ArrayBuffer) : null)
    xhr.onerror = () => resolve(null)
    xhr.send()
  })
  if (data) await useModel({ kind: 'vrm', data }, !announce)
}

async function onFile(e: Event): Promise<void> {
  const file = (e.target as HTMLInputElement).files?.[0]
  if (file) await useModel({ kind: 'vrm', data: await file.arrayBuffer() })
}

// ---------- 鼠标：穿透、视线、点击、拖动 ----------

let lastHoverCheck = 0
let acceptingMouse = false

/** 鼠标在界面控件（对话框等）上 */
function overUi(x: number, y: number): boolean {
  return document.elementFromPoint(x, y)?.closest('[data-interactive]') != null
}

/** 只有鼠标在角色或对话框上时窗口才接收点击，其他地方点击会穿透到桌面 */
function updateHover(x: number, y: number): void {
  // 读像素会让 GPU 等待，限制在每秒最多 25 次
  const now = performance.now()
  if (now - lastHoverCheck < 40) return
  lastHoverCheck = now
  overAvatar.value = stage?.hitTest(x, y) != null
  const accept = overAvatar.value || overUi(x, y)
  if (accept === acceptingMouse) return
  acceptingMouse = accept
  bridge?.setIgnoreMouse(!accept)
}

function lookAt(x: number, y: number): void {
  if (!stage) return
  lastCursorMove = performance.now()
  behavior.cursorMoved(stage.screenToLookTarget(x, y, lookTarget))
}

let press: { x: number; y: number; screenX: number; region: HitRegion } | null = null
let dragging = false
/** 单击要等一下，看是不是双击（双击打开对话框，不算戳她） */
const DOUBLE_CLICK_MS = 260
let pendingPoke = 0

function onPointerDown(e: PointerEvent): void {
  lastInteraction = performance.now()
  if (e.button !== 0 || !stage) return
  const region = stage.hitTest(e.clientX, e.clientY)
  if (!region) return
  press = { x: e.clientX, y: e.clientY, screenX: e.screenX, region }
  ;(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
}

function onPointerMove(e: PointerEvent): void {
  if (!press) return
  if (!dragging && Math.hypot(e.clientX - press.x, e.clientY - press.y) > 4) {
    dragging = true
    bridge?.dragStart()
    behavior.dragStart()
  }
  if (dragging) {
    bridge?.dragMove()
    behavior.dragMove(e.screenX - press.screenX)
    press.screenX = e.screenX
  }
}

function onPointerUp(): void {
  if (!press) return
  if (dragging) {
    bridge?.dragEnd()
    behavior.dragEnd()
  } else if (pendingPoke) {
    clearTimeout(pendingPoke)
    pendingPoke = 0
    void openChat()
  } else {
    const region = press.region
    pendingPoke = window.setTimeout(() => {
      pendingPoke = 0
      behavior.poke(region)
    }, DOUBLE_CLICK_MS)
  }
  press = null
  dragging = false
}

function onContextMenu(e: MouseEvent): void {
  if (!stage?.hitTest(e.clientX, e.clientY)) return
  if (bridge) bridge.showMenu(switches())
  else fileInput.value?.click() // 浏览器调试时直接选文件
}

function onMouseMove(e: MouseEvent): void {
  updateHover(e.clientX, e.clientY)
  if (!bridge) lookAt(e.clientX, e.clientY) // 桌面版的视线由主进程的全局光标驱动
}

// ---------- 启动 ----------

/** 扩展包带的动作和台词（AI 服务没开、没装扩展时就是内置的那些） */
async function loadCompanion(): Promise<void> {
  const res = await bridge?.extCompanion()
  const c = res?.ok ? res.data : undefined
  behavior.setExtraGestures(cleanMotions(c?.motions, BUILTIN_GESTURES))
  applyExtensionLines(c?.lines)
}

onMounted(async () => {
  stage = new Stage(stageEl.value!, behavior)
  stage.onStep = (dt) => {
    conversation.tick(dt)
    // 忙的时候看到的报错，忙完（而且方便说话了）再问
    const free = !conversation.busy && !convAsk.value && !screenAsk.value && !reminderAsk.value
    if (pendingFinding && free && !desk.value?.presence.quiet) askAboutFinding(pendingFinding)
    // 问别的事时到点的提醒：问完再说
    if (pendingReminders.length && free) showReminder(pendingReminders.shift()!)
  }
  stage.maxFps = frameLimit
  stage.onAvatarFramed = (top) => {
    avatarTop.value = `${top}px`
  }
  stage.setAvatar(new PlaceholderAvatar())
  stage.start()
  if (import.meta.env.DEV) {
    // 开发调试入口：控制台里可以直接操作舞台、行为、对话和语音
    ;(window as unknown as Record<string, unknown>).__aila = {
      stage,
      behavior,
      conversation,
      openChat,
      voice: voiceControl,
      screen: screenWatch,
      speaker,
      hear,
      loadBuiltin,
      openSettings: (section?: Parameters<NonNullable<typeof bridge>['openSettings']>[0]) => bridge?.openSettings(section)
    }
    console.log(`[aila] 舞台已启动，渲染器：${stage.rendererInfo()}`)
  }

  document.addEventListener('mousemove', onMouseMove)
  if (bridge) {
    subscriptions.push(
      bridge.onCursor((p) => {
        lookAt(p.x, p.y)
        // 拖着文件时系统不发鼠标移动：靠主进程推的光标位置判断是不是在她身上，在的话窗口才接得住拖进来的文件
        if (p.x >= 0 && p.y >= 0 && p.x < window.innerWidth && p.y < window.innerHeight) updateHover(p.x, p.y)
      }),
      bridge.onModelLoaded((data) => void useModel(data)),
      bridge.onModelBuiltin(() => void loadBuiltin(true)),
      bridge.onChatOpen(() => void openChat()),
      bridge.onVoiceMenu(onVoiceMenu),
      // 星图里点了「问她」「让她修」、主窗口对话页打的字：当成用户说的一句话
      bridge.onChatSay((text) => {
        clearScreenAsk()
        walkIfAsked(text)
        conversation.send(text)
      }),
      // 星图里双手合十：确认的必须正是她现在问的这一份修改
      bridge.onGestureApprove((id) => {
        if (convAsk.value?.id === id) conversation.answer(true)
      }),
      bridge.onDesktopStatus(applyDesktop),
      bridge.onClipboardAsk(() => void sendWithClipboard(t('帮我看看剪贴板里的这段报错'))),
      // 扩展包：装卸、开关以后重新拿动作和台词；扩展页点了「试一下」就做那个动作
      bridge.onExtChanged(() => void loadCompanion()),
      bridge.onExtPlay((name) => behavior.gesture(name))
    )
    void loadCompanion()
    // 说出来的话提到剪贴板，也先带上剪贴板的内容
    // 回复里的 [go-topright]：说得随意的（「躲开点」）由大模型决定去哪
    conversation.onMove = (spot) => bridge?.moveTo(spot)
    // 主窗口的对话页：回复的进度推过去，那边点的「好 / 不要」「停下」交回来
    conversation.onLive = (r) => bridge?.chatLive({ ...r, ask: r.ask ? { ...r.ask } : null })
    subscriptions.push(
      bridge.onChatAnswer((id, approved) => {
        if (convAsk.value?.id === id) conversation.answer(approved)
      }),
      bridge.onChatStop(() => conversation.interrupt())
    )
    subscriptions.push(
      bridge.onEntrance(({ phase, first, quiet }) => {
        if (phase === 'hide') return void (entrancePhase.value = 'waiting')
        entrancePhase.value = phase
        behavior.entrance(phase, first, quiet)
        if (phase === 'land') setTimeout(() => entrancePhase.value === 'land' && (entrancePhase.value = ''), 600)
      })
    )
    // 开场演出（shared/intro.ts）：导演让她说的话、做的动作、问借不借鼠标
    subscriptions.push(
      bridge.onIntroCue((c) => {
        if (c.gesture === 'knock' || c.gesture === 'turn' || c.gesture === 'point' || c.gesture === 'yawn') behavior.introGesture(c.gesture)
        else if (c.gesture) behavior.gesture(c.gesture)
        if (!c.askMouse) introAsk.value = false // 导演那边不等了（超时当不借、被跳过）：问题收起来
        if (c.askMouse) {
          introAsk.value = true
          behavior.say(c.say ?? INTRO_LINES.askMouse, 60_000)
        } else if (c.say) {
          // 开着「说话」的话真的念出来（气泡、声音、口型一起），没开就只冒气泡
          if (voice.enabled) conversation.announce(c.say)
          else behavior.say(c.say, c.ms ?? 3000)
        }
      })
    )
    // 叫她去的：到了点点头；自己闲逛到的：偶尔伸个懒腰
    subscriptions.push(
      bridge.onArrived((target) => {
        if (target !== 'stroll') behavior.gesture('nod')
        else if (Math.random() < 0.3) behavior.gesture('stretch')
      })
    )
    // 设置里开了「没事时走走」：隔一阵自己挪几步（打字、全屏、离开电脑由主进程再把一道关）
    const strollTimer = setInterval(() => {
      const s = desk.value
      if (!s) return
      const ok = shouldStroll({
        enabled: s.prefs.wander,
        visible: s.presence.visible,
        quiet: s.presence.quiet,
        chatting: chatOpen.value || conversation.busy,
        asleep: behavior.isSleepy
      })
      if (ok) bridge?.moveTo('stroll')
    }, 40_000)
    subscriptions.push(() => clearInterval(strollTimer))
    voiceControl.onHeard = (text) => {
      walkIfAsked(text)
      if (!mentionsClipboard(text)) return false
      void sendWithClipboard(text)
      return true
    }
    void bridge.desktopStatus().then(applyDesktop)
  }
  void voiceControl.init()
  screenWatch.init()
  await connectHistory()

  // 用户自己选过模型就用那个；没选过就用自带的原创形象（public/avatar/aila.vrm，scripts/avatar 生成的）
  const last = await bridge?.getLastModel()
  if (last) await useModel(last, true)
  else await loadBuiltin()

  // 报到：要出场的话等落地再打招呼（Behavior.entrance），不出场就像以前一样直接打招呼
  const entering = (await bridge?.characterReady?.().catch(() => false)) ?? false
  if (!entering) {
    if (entrancePhase.value === 'waiting') entrancePhase.value = ''
    setTimeout(() => behavior.greet(), 600)
  }
  setTimeout(() => {
    if (usingPlaceholder) behavior.say(lines.noModel, 4000)
  }, 4800)
})

onBeforeUnmount(() => {
  document.removeEventListener('mousemove', onMouseMove)
  // 开发时改了 preload 没重启，旧的订阅函数不返回退订函数：跳过，别让卸载出错
  for (const off of subscriptions.splice(0)) if (typeof off === 'function') off()
  clearTimeout(reminderTimer)
  clearTimeout(briefingTimer)
  clearTimeout(bubbleTimer)
  conversation.interrupt()
  voiceControl.dispose()
  screenWatch.dispose()
  stage?.dispose()
})
</script>

<template>
  <div class="root" :class="{ grab: overAvatar }" @contextmenu.prevent="onContextMenu" @dragover="onDragOver" @drop.prevent="onDrop">
    <div
      ref="stageEl"
      class="stage"
      :class="entrancePhase"
      @pointerdown="onPointerDown"
      @pointermove="onPointerMove"
      @pointerup="onPointerUp"
    ></div>
    <div class="dialogue" :style="{ '--avatar-top': avatarTop }">
      <SpeechBubble
        :text="bubbleText"
        :visible="bubbleVisible"
        :ask="bubbleAsk"
        :full-text="bubbleFullText"
        :expanded="bubbleExpanded"
        @answer="onAnswer"
        @toggle-full-text="toggleBubbleFullText"
      />
      <ChatInput v-if="chatOpen" ref="chatInput" @send="onSend" @close="closeChat" />
    </div>
    <!-- 开着「留意屏幕上的报错」时一直显示：她在看 -->
    <div v-if="watching" class="watching" :title="t('正在留意屏幕上的报错（只看写代码的窗口）')">{{ t('● 正在看屏幕') }}</div>
    <input ref="fileInput" type="file" accept=".vrm" hidden @change="onFile" />
  </div>
</template>

<style>
html,
body,
#app {
  margin: 0;
  width: 100%;
  height: 100%;
  overflow: hidden;
  background: transparent;
  font-family: 'Microsoft YaHei', 'PingFang SC', sans-serif;
  user-select: none;
}

.root,
.stage {
  position: relative;
  width: 100%;
  height: 100%;
  transform-origin: 50% 100%; /* 从脚底缩放：蹲下、落地时脚不离地 */
}

/* 出场：等着 → 从卡片里冒出来 → 蹲一下 → 空中拉长 → 落地压扁再弹回来 */
.stage.waiting {
  opacity: 0;
}

.stage.emerge {
  animation: emerge 0.65s cubic-bezier(0.2, 1.35, 0.4, 1) both;
}

.stage.crouch {
  transform: scale(1.05, 0.9);
  transition: transform 0.2s ease-out;
}

.stage.jump {
  animation: hop 1.05s ease-in-out both;
}

.stage.land {
  animation: land 0.55s ease-out both;
}

@keyframes emerge {
  from {
    opacity: 0;
    transform: translateY(16%) scale(0.35);
  }
  45% {
    opacity: 1;
  }
  to {
    transform: none;
  }
}

@keyframes hop {
  0% {
    transform: scale(1.05, 0.9);
  }
  14% {
    transform: scale(0.94, 1.08);
  }
  70% {
    transform: scale(0.98, 1.03);
  }
  100% {
    transform: none;
  }
}

@keyframes land {
  0% {
    transform: scale(1.1, 0.86);
  }
  45% {
    transform: scale(0.97, 1.04);
  }
  100% {
    transform: none;
  }
}

.root.grab {
  cursor: grab;
}

.dialogue {
  position: absolute;
  inset: 10px 10px calc(100% - max(20px, var(--avatar-top)) + 12px);
  display: flex;
  flex-direction: column;
  justify-content: flex-end;
  align-items: center;
  gap: 8px;
  pointer-events: none;
}

.watching {
  position: absolute;
  bottom: 6px;
  left: 50%;
  transform: translateX(-50%);
  padding: 1px 8px;
  border-radius: 9px;
  background: rgba(40, 46, 70, 0.55);
  color: #fff;
  font-size: 11px;
  white-space: nowrap;
  pointer-events: none;
}
</style>
