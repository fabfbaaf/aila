/**
 * 对话记录页：把聊天记录按天分组、按关键词筛选（纯逻辑，有测试）。
 * 记录本身早就存在本机（userData/history.json，登录后经云端同步），这一页只是把它摆出来看。
 */

import type { StoredMessage } from '../../../shared/cloud'

export interface DayGroup {
  /** 今天 / 昨天 / 11 月 14 日（星期五） */
  label: string
  /** 这一天的日期键 YYYY-MM-DD（本地时间） */
  key: string
  items: StoredMessage[]
}

const WEEKDAYS = '日一二三四五六'

function dayKey(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

export function dayLabel(d: Date, now: Date): string {
  const key = dayKey(d)
  if (key === dayKey(now)) return '今天'
  if (key === dayKey(new Date(now.getTime() - 24 * 3600 * 1000))) return '昨天'
  const year = d.getFullYear() === now.getFullYear() ? '' : `${d.getFullYear()} 年 `
  return `${year}${d.getMonth() + 1} 月 ${d.getDate()} 日（星期${WEEKDAYS[d.getDay()]}）`
}

/**
 * 删掉的不显示；有关键词时只留含关键词的消息（不分大小写）。
 * 新的一天在上面，一天之内按时间先后（读起来像聊天记录）。
 */
/** oldestFirst：对话页像聊天一样，早的在上、新的在下；默认新的一天在上（翻记录用） */
export function groupHistory(messages: StoredMessage[], query: string, now: Date, oldestFirst = false): DayGroup[] {
  const q = query.trim().toLowerCase()
  const kept = messages
    .filter((m) => !m.deleted && (!q || m.content.toLowerCase().includes(q)))
    .sort((a, b) => (a.createdAt < b.createdAt ? -1 : a.createdAt > b.createdAt ? 1 : 0))
  const groups = new Map<string, DayGroup>()
  for (const m of kept) {
    const d = new Date(m.createdAt)
    if (Number.isNaN(d.getTime())) continue
    const key = dayKey(d)
    let g = groups.get(key)
    if (!g) {
      g = { key, label: dayLabel(d, now), items: [] }
      groups.set(key, g)
    }
    g.items.push(m)
  }
  const days = [...groups.values()].sort((a, b) => (a.key < b.key ? 1 : -1))
  return oldestFirst ? days.reverse() : days
}

/** 存下来的她的话里带着表情、动作、走动标签（[nod]、[happy]、[go-topright]……），给人看时去掉 */
export function cleanText(text: string): string {
  return text.replace(/\[(?:happy|sad|surprised|relaxed|neutral|nod|shake|tilt|think|bow|wave|stretch|go-[a-z]+)\]/g, '').trim()
}

export function timeOf(iso: string): string {
  const d = new Date(iso)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}
