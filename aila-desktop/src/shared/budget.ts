/** 金额在桥接和任务页使用同一套校验；小额已知费用保留精度，未知费用由调用方单独显示。 */

export function validBudget(value: unknown): value is number | null {
  return value === null || (typeof value === 'number' && Number.isFinite(value) && value >= 0)
}

export function parseBudget(text: string): number | null {
  const value = text.trim()
  if (!value) return null
  const amount = Number(value)
  if (!/^(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(value) || !validBudget(amount)) {
    throw new Error('花费上限要填有限的非负金额，留空表示不限。')
  }
  return amount
}

export function yuan(amount: number): string {
  if (amount > 0 && amount < 0.000001) return '不到 ¥0.000001'
  return `¥${amount.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 6 })}`
}
