/** 气泡下方等用户回答的那一块：标签（哪个技能包 / 哪个程序）、具体内容、几个按钮 */
export interface BubbleAsk {
  tag: string
  detail: string
  /** 小字说明，比如「同意后把附近 12 行发给大模型」 */
  note?: string
  buttons: { label: string; value: string; primary?: boolean }[]
}
