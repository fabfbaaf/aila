/**
 * 任务页左边的会话列表：按时间分组（今天 / 昨天 / 这周 / 更早），以及删除前给你看的那句话。
 * 抽成纯函数方便单测；界面只管画。
 */
import type { AgentSessionSummary } from '../../../shared/agent'
import { t } from '../i18n'

export interface SessionGroup {
  label: string
  items: AgentSessionSummary[]
}

const DAY = 86400000

/** 电脑任务用固定名字，旧服务没有 kind 的会话继续显示项目名。 */
export function sessionLabel(s: Pick<AgentSessionSummary, 'cwd' | 'kind'>): string {
  return s.kind === 'computer' ? t('电脑') : s.cwd.replace(/[\\/]+$/, '').split(/[\\/]/).pop() || s.cwd
}

/** 分组；输入已经是最近的在前，组内保持原顺序。now 是毫秒时间戳（测试里固定） */
export function groupSessions(list: AgentSessionSummary[], now = Date.now()): SessionGroup[] {
  const today = new Date(now)
  today.setHours(0, 0, 0, 0)
  const start = today.getTime()
  const groups: SessionGroup[] = [
    { label: t('今天'), items: [] },
    { label: t('昨天'), items: [] },
    { label: t('这周'), items: [] },
    { label: t('更早'), items: [] }
  ]
  for (const s of list) {
    const at = (s.updated ?? 0) * 1000
    const index = at >= start ? 0 : at >= start - DAY ? 1 : at >= start - 6 * DAY ? 2 : 3
    groups[index].items.push(s)
  }
  return groups.filter((g) => g.items.length)
}

/** 删除确认框里的说明：按这个会话的情况由代码生成 */
export function deleteNotice(s: Pick<AgentSessionSummary, 'title' | 'branch' | 'running' | 'kind'>): string[] {
  const lines = [t('会话记录（她说过的话、改文件的过程、用量）会删掉，删了找不回来。')]
  if (s.branch) lines.push(t('她在分支 {branch} 上的临时目录会一起放弃；已经合并回去的改动不受影响。', { branch: s.branch }))
  lines.push(s.kind === 'computer' ? t('你电脑里的文件不会动。') : t('你项目里的文件不会动。'))
  return lines
}
