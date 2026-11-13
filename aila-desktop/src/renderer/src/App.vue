<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { Vector3 } from 'three'
import SpeechBubble from './components/SpeechBubble.vue'
import ChatInput from './components/ChatInput.vue'
import type { CharacterEvent } from '../../shared/settings'
import type { ConfirmAsk } from '../../shared/chat'
import { Behavior } from './behavior/Behavior'
import { Conversation, type Voice } from './chat/Conversation'
import { createTransport } from './chat/transport'
import { Stage, type HitRegion } from './scene/Stage'
import { PlaceholderAvatar } from './avatar/PlaceholderAvatar'
import { loadVrmAvatar } from './avatar/VrmAvatar'
import { lines, pick } from './persona/lines'
import { bridge } from './platform/bridge'
import { Speaker } from './voice/Speaker'
import { VoiceController } from './voice/VoiceController'
import { VoiceLink } from './voice/VoiceLink'
import { MIC_SAMPLE_RATE } from './voice/Microphone'
import type { VoiceMenuAction } from '../../shared/voice'

const stageEl = ref<HTMLDivElement>()
const fileInput = ref<HTMLInputElement>()
const chatInput = ref<InstanceType<typeof ChatInput>>()
const bubbleText = ref('')
const bubbleVisible = ref(false)
const overAvatar = ref(false)
const chatOpen = ref(false)
/** 她在等用户确认的操作（气泡里显示「好 / 不要」） */
const ask = ref<ConfirmAsk | null>(null)

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
let stage: Stage | null = null
let usingPlaceholder = true
let bubbleTimer = 0
const lookTarget = new Vector3()

/** holdMs 为 null 时一直显示，直到下一次更新 */
function showBubble(text: string, holdMs: number | null): void {
  bubbleText.value = text
  bubbleVisible.value = true
  clearTimeout(bubbleTimer)
  if (holdMs !== null) bubbleTimer = window.setTimeout(() => (bubbleVisible.value = false), holdMs)
}

behavior.onSay = showBubble
conversation.onBubble = showBubble
voiceControl.onBubble = showBubble
conversation.onAsk = (a) => (ask.value = a)

function onVoiceMenu(action: VoiceMenuAction): void {
  if (action === 'toggle-wake') void voiceControl.toggleWake()
  else if (action === 'toggle-speak') voiceControl.toggleSpeak()
  else void voiceControl.listenNow()
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
}

function onSend(text: string): void {
  // 她在等确认时打「好」「不要」就是回答；打别的话当新话题（这个操作不执行）
  if (conversation.answerText(text)) return
  conversation.send(text)
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
      behavior.say(ev.names.length === 1 ? `……《${ev.names[0]}》我读完了。` : `……${ev.names.length} 份资料我都读完了。`, 3500)
      break
    case 'settings-saved':
      behavior.gesture('nod')
      break
  }
}

/** 对话记录：启动时从本地恢复；说出口的话交给主进程保存、同步；别的设备的话合并进来 */
async function connectHistory(): Promise<void> {
  if (!bridge) return // 浏览器调试时只在内存里
  const api = bridge
  conversation.restore(await api.historyLoad())
  conversation.onCommit = (m) => api.historyAppend(m)
  api.onHistoryRemote((changes) => {
    conversation.applyRemote(changes)
    for (const s of changes.settings) voiceControl.applySetting(s.key, s.value, s.deleted)
  })
  voiceControl.onSettingChanged = (key, value) => api.settingPut(key, value)
  // 语音开关存在这里，设置窗口通过主进程读写
  voiceControl.onPrefsChanged = (prefs) => api.voiceReport(prefs)
  api.voiceReport(voiceControl.menuState())
  api.onCharacterEvent(onCharacterEvent)
}

// ---------- 模型 ----------

async function useModel(data: ArrayBuffer): Promise<void> {
  try {
    const avatar = await loadVrmAvatar(data)
    stage?.setAvatar(avatar)
    usingPlaceholder = false
    behavior.react('happy')
    behavior.say(lines.modelLoaded)
  } catch (err) {
    console.error('加载 VRM 失败', err)
    behavior.react('sad')
    behavior.say(lines.modelFailed)
  }
}

async function onFile(e: Event): Promise<void> {
  const file = (e.target as HTMLInputElement).files?.[0]
  if (file) await useModel(await file.arrayBuffer())
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
  behavior.cursorMoved(stage.screenToLookTarget(x, y, lookTarget))
}

let press: { x: number; y: number; screenX: number; region: HitRegion } | null = null
let dragging = false
/** 单击要等一下，看是不是双击（双击打开对话框，不算戳她） */
const DOUBLE_CLICK_MS = 260
let pendingPoke = 0

function onPointerDown(e: PointerEvent): void {
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
  if (bridge) bridge.showMenu(voiceControl.menuState())
  else fileInput.value?.click() // 浏览器调试时直接选文件
}

function onMouseMove(e: MouseEvent): void {
  updateHover(e.clientX, e.clientY)
  if (!bridge) lookAt(e.clientX, e.clientY) // 桌面版的视线由主进程的全局光标驱动
}

// ---------- 启动 ----------

onMounted(async () => {
  stage = new Stage(stageEl.value!, behavior)
  stage.onStep = (dt) => conversation.tick(dt)
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
      speaker,
      hear,
      openSettings: (section?: Parameters<NonNullable<typeof bridge>['openSettings']>[0]) => bridge?.openSettings(section)
    }
    console.log(`[aila] 舞台已启动，渲染器：${stage.rendererInfo()}`)
  }

  document.addEventListener('mousemove', onMouseMove)
  bridge?.onCursor((p) => lookAt(p.x, p.y))
  bridge?.onModelLoaded((data) => void useModel(data))
  bridge?.onChatOpen(() => void openChat())
  bridge?.onVoiceMenu(onVoiceMenu)
  void voiceControl.init()
  await connectHistory()

  const last = await bridge?.getLastModel()
  if (last) await useModel(last)

  setTimeout(() => behavior.greet(), 600)
  setTimeout(() => {
    if (usingPlaceholder) behavior.say(lines.noModel, 4000)
  }, 4800)
})

onBeforeUnmount(() => {
  document.removeEventListener('mousemove', onMouseMove)
  conversation.interrupt()
  voiceControl.dispose()
  stage?.dispose()
})
</script>

<template>
  <div class="root" :class="{ grab: overAvatar }" @contextmenu.prevent="onContextMenu">
    <div
      ref="stageEl"
      class="stage"
      @pointerdown="onPointerDown"
      @pointermove="onPointerMove"
      @pointerup="onPointerUp"
    ></div>
    <SpeechBubble :text="bubbleText" :visible="bubbleVisible" :ask="ask" @answer="(ok) => conversation.answer(ok)" />
    <ChatInput v-if="chatOpen" ref="chatInput" @send="onSend" @close="closeChat" />
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
}

.root.grab {
  cursor: grab;
}
</style>
