// 浏览器和桌面转发使用同一组固定的任务管理接口。
import { describe, expect, it } from 'vitest'
import { taskWorkRoute, type TaskWorkRequest } from './taskWork'

describe('taskWorkRoute', () => {
  it('把项目目录和原生会话翻页信息放在查询参数中', () => {
    const result = taskWorkRoute({ action: 'native-list', engine: 'codex-alt', cwd: 'C:/带 & 符号', cursor: '+/#' })
    const url = new URL(result!.path, 'http://localhost')
    expect(result?.method).toBe('GET')
    expect(url.pathname).toBe('/agent/taskwork/native')
    expect(url.searchParams.get('cwd')).toBe('C:/带 & 符号')
    expect(url.searchParams.get('cursor')).toBe('+/#')
  })

  it('只转发已知操作，IPC 消息不能自定接口路径', () => {
    expect(taskWorkRoute({ action: 'open', path: '/settings' } as unknown as TaskWorkRequest)).toBeUndefined()
    expect(taskWorkRoute({ action: 'cancel', id: '..' })).toBeUndefined()
    expect(taskWorkRoute({ action: 'group-session', group: 'key', member: 'sid' })).toEqual({
      method: 'POST', path: '/agent/taskwork/group-session', body: { group: 'key', member: 'sid' }
    })
  })
})
