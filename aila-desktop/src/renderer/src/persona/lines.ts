// 艾拉的台词：句子短、礼貌、偶尔停顿，不撒娇、不长篇大论
import type { ChatErrorCode } from '../../../shared/chat'

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
  forgetAll: '……都忘掉了。我们重新认识一下吧。'
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
