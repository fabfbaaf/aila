/** 气泡下方等用户回答的那一块：标签（哪个技能包 / 哪个程序）、具体内容、几个按钮 */
export interface BubbleAsk {
  tag: string
  detail: string
  /** 小字说明，比如「同意后把附近 12 行发给大模型」 */
  note?: string
  buttons: { label: string; value: string; primary?: boolean }[]
}

export type DiffKind = 'file' | 'hunk' | 'add' | 'del' | ''

/** 按行拆开；是代码改动（diff）的行标上种类：加的行标绿、删的行标红 */
export function diffLines(detail: string): { text: string; kind: DiffKind }[] {
  return detail.split('\n').map((text) => ({
    text: text || ' ',
    kind:
      text.startsWith('+++') || text.startsWith('---')
        ? 'file'
        : text.startsWith('@@')
          ? 'hunk'
          : text.startsWith('+')
            ? 'add'
            : text.startsWith('-')
              ? 'del'
              : ''
  }))
}
