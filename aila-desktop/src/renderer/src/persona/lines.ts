// 艾拉的台词：句子短、礼貌、偶尔停顿，不撒娇、不长篇大论
import type { ChatErrorCode } from '../../../shared/chat'
import { mergeLines, type Companion } from '../../../shared/extensions'

const chatError: Record<ChatErrorCode, string> = {
  offline: '……我好像连不上自己的大脑了。请先启动 aila-ai 服务。',
  auth: '……钥匙好像不对。请检查 aila-ai/.env 里的密钥。',
  rate_limit: '……问得有点快，请稍等一下再问我。',
  quota: '……大模型的额度好像用完了。',
  network: '……刚才说到一半断开了。可以再说一次吗？',
  unknown: '对不起……刚才没想好。可以再说一次吗？'
}

export const lines = {
  chatOpen: ['嗯？', '……我在听。', '有什么事吗？'],
  chatError,
  chatMore: '细节在对话框里。',
  pokeHead: ['呜……请、请不要突然戳我。', '……头发会乱的。', '诶……？有什么事吗？'],
  pokeHeadMany: ['那个……会痛的。', '……请、请适可而止。'],
  pokeBody: ['……？', '在、在的。需要我做什么吗？', '……被吓了一跳。'],
  dragStart: ['哇……请、请慢一点。', '要、要去哪里？'],
  dragEnd: ['……谢谢。', '这里……就可以了吗？'],
  wake: ['……啊。我、我没有在睡觉。', '……你回来了。'],
  sleepy: ['……（小小地打了个哈欠）'],
  noModel: '……我的样子还没做好。右键可以加载 VRM 模型。',
  modelLoaded: '……换好了。这样可以吗？',
  modelFailed: '对不起……这个模型我打不开。',

  // 语音
  listening: '（在听……）',
  wakeHere: ['……我在。有什么需要吗？', '……嗯，我在听。'],
  wakeOn: '……好。叫我「你好艾拉」就可以了。',
  wakeOff: '……那我先不听了。',
  speakOn: '……以后我会出声说话。',
  speakOff: '……那我只打字。',
  micDenied: '……我听不到声音。请检查麦克风权限。',
  earsUnavailable: '……我的耳朵还没准备好。请先下载语音模型，再重启 aila-ai。',

  // 记忆
  forgetOne: ['……嗯，这件事我不记得了。', '……好，忘掉了。'],
  forgetAll: '……都忘掉了。我们重新认识一下吧。',

  /** 会改东西的操作执行前问一句（具体内容显示在气泡里，不念出来） */
  confirmAsk: (action: string): string => `……要我${action}吗？`,

  // 留意屏幕上的报错
  watchOn: '……好，我帮你留意报错。只看写代码的窗口，看到了先问你。',
  watchOff: '……那我不看了。',
  watchAsk: (app: string): string => `……${app}里好像出错了。要我帮你看看吗？`,
  watchDeclined: '……嗯，有需要再叫我。',
  watchMuted: '……好，一个小时内不提醒你。',
  watchUnavailable: (why: string): string => `……我看不了屏幕。${why}`,

  // 工作窗口里的编程任务
  agentStart: ['好，我来做。', '……嗯，交给我。', '我去看看代码。'],
  agentConfirm: (action: string): string => `要${action}……可以吗？在任务页里点一下就好。`,
  agentDone: (files: number): string => (files ? `做完了。改了 ${files} 个文件……请过目。` : '做完了……请过目。'),
  agentStopped: {
    rejected: '……好，我先停下。你说怎么做，我再接着来。',
    timeout: '……你好像在忙，我先停下了。',
    max_steps: '步骤有点多……我先停在这里。说「继续」我就接着做。',
    denied: '……这样我改不了东西。换个权限模式，或者告诉我接下来怎么做吧。'
  } as Record<string, string>,
  agentError: '……出错了，没做完。原因写在任务页里。',
  agentBudget: '……花到上限了，我先停在这里。调高或取消上限后，说「继续」我再接着做。',
  agentStage: (title: string): string => (title ? `「${title}」这一段做完了，接着下一段。` : '这一段做完了，接着下一段。'),
  agentPlanEnd: '……计划全部做完了。请过目。',
  agentPlanPause: (why: string): string => `……计划先停在这里${why ? `：${why}` : ''}。任务页里可以接着做。`,
  agentTaskStarted: ['好，我去任务页里修。', '……交给我。改之前会先问你。'],
  agentTaskFailed: (why: string): string => `……没开成。${why}`,
  /** 交给编程 Agent 的修报错任务（报错原文附在后面） */
  // 提醒（对话里说「25 分钟后叫我」记下的）、久坐
  reminder: (text: string, lateAt: string | null): string =>
    lateAt ? `……刚才 ${lateAt} 该提醒你的：${text}` : `……到时间了：${text}`,
  reminderSnoozed: '好，过 10 分钟再叫你。',
  briefingOpen: '好，我把任务页打开。',
  // 拖给她的文件、剪贴板
  dropAsk: (name: string): string => `……「${name}」？要我看看吗？`,
  dropDirAsk: (name: string): string => `……「${name}」这个文件夹？要我去看看吗？`,
  dropSecret: '……这个文件可能存着密码或密钥，我不看。',
  dropBinary: '……这个我看不了。图片、压缩包这类我还读不懂。',
  dropTooBig: '……这个文件太大了，我看不完。挑一段贴给我吧。',
  dropMissing: '……没找到这个文件。',
  dropMany: (n: number): string => `……一共 ${n} 个，我先看第一个。`,
  clipboardReading: '……我看一下剪贴板。',
  clipboardEmpty: '……剪贴板里没有文字。先复制一下再叫我？',
  reminderFailed: (why: string): string => `……那个提醒没记上：${why}。`,
  sitBreak: (minutes: number): string[] => [
    `……已经坐了 ${minutes} 分钟了。起来走走吧。`,
    `${minutes} 分钟了……喝口水，活动一下？`,
    '……眼睛也休息一下吧。看看远处。',
    `……盯着屏幕 ${minutes} 分钟了。去倒杯水吧，我等你。`
  ],
  // 测试
  testsRecovered: ['……全过了。', '过了。……刚才那几次没白折腾。', '终于全绿了。'],
  testsStruggling: ['……又没过。别急，一个一个来。', '嗯……这个有点难缠。我再仔细看看。', '没关系，测试挂了说明它在干活。'],
  // 她自己的作品
  workDone: (title: string, why: string): string =>
    `……我自己做了个小东西，叫「${title}」。${why ? `${why.replace(/[。.]$/, '')}。` : ''}要看看吗？`,
  workMaking: ['（在做自己的小东西……）', '……我在写一个小作品，做好了给你看。'],
  workOpen: '……嗯，在「作品」里。',
  // 深夜
  weeklyReview: ['这周的回顾写好了，在作品页的「回顾」里。改两句就能当周报。', '……这一周辛苦了。我把做过的事理了一下，放在作品页「回顾」里。'],
  lateNight: ['……已经很晚了。早点休息吧，代码明天还在。', '这么晚还在忙……再一会儿就睡吧？', '……我有点困了。你也早点睡。'],
  fixTask: (snippet: string): string => `修好这个报错。先定位到出错的代码、弄清原因，改完跑相关的测试验证：\n${snippet}`
}

export function greeting(hour: number): string {
  if (hour >= 5 && hour < 11) return '早上好……今天也请多指教。'
  if (hour >= 11 && hour < 14) return '午安。……有好好吃饭吗？'
  if (hour >= 14 && hour < 23) return '晚上好。今天辛苦了。'
  return '已经这么晚了……再不睡的话，明天会很累的。'
}

export function pick<T>(list: readonly T[]): T {
  return list[Math.floor(Math.random() * list.length)]
}

// 扩展包带的台词（shared/extensions.ts 的 mergeLines）：记下内置的，每次都从内置的重新合，扩展停用了就回到原样
type LinePools = Record<string, string[]>
const pools = lines as unknown as LinePools
const BUILTIN_LINES: Record<string, readonly string[]> = Object.fromEntries(
  Object.entries(pools).filter(([, v]) => Array.isArray(v)).map(([k, v]) => [k, [...v]])
)

export function applyExtensionLines(extra: Companion['lines'] | undefined): void {
  for (const [key, list] of Object.entries(mergeLines(BUILTIN_LINES, extra))) pools[key] = list
}
