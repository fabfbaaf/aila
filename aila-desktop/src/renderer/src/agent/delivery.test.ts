import { describe, expect, it } from 'vitest'
import type { DeliveryCard } from '../../../shared/delivery'
import { validAcceptance } from '../../../shared/delivery'
import { brief, commentTarget, hasDelivery, parseDiff, putComment } from './delivery'

const DIFF = [
  'diff --git a/src/cart.py b/src/cart.py',
  'index 1..2 100644',
  '--- a/src/cart.py',
  '+++ b/src/cart.py',
  '@@ -1,3 +1,4 @@',
  ' def total(items):',
  '-    return sum(items)',
  '+    if not items:',
  '+        return 0',
  '+    return sum(items)',
  '@@ -10,2 +11,2 @@ def tax():',
  ' ',
  '-x = 1',
  '+x = 2',
  '\\ No newline at end of file',
  ''
].join('\n')

function card(over: Partial<DeliveryCard> = {}): DeliveryCard {
  return { changes: { files: [], base: 'HEAD', note: '' }, tests: null, branch: null, pr: null, ci: null, acceptance: null, verdict: null, ...over }
}

describe('交付卡', () => {
  it('diff 拆成一行行，带改前改后的行号', () => {
    const lines = parseDiff(DIFF)
    expect(lines.filter((l) => l.kind === 'meta').map((l) => l.text)).toEqual([
      'diff --git a/src/cart.py b/src/cart.py', 'index 1..2 100644', '--- a/src/cart.py', '+++ b/src/cart.py', '\\ No newline at end of file'
    ])
    const body = lines.filter((l) => l.kind !== 'meta' && l.kind !== 'hunk')
    expect(body.map((l) => [l.kind, l.old, l.new])).toEqual([
      ['ctx', 1, 1], ['del', 2, null], ['add', null, 2], ['add', null, 3], ['add', null, 4],
      ['ctx', 10, 11], ['del', 11, null], ['add', null, 12]
    ])
    expect(body[0].text).toBe('def total(items):')
    expect(parseDiff('--- /dev/null\n+++ b/new.py\n@@ -0,0 +1,2 @@\n+a\n+b\n… 还有 3 行没显示').map((l) => l.kind)).toEqual(['meta', 'meta', 'hunk', 'add', 'add', 'more'])
    expect(parseDiff('')).toEqual([])
  })

  it('加的行、没动的行记改后的行号，删掉的行记改前的；标题行不能点', () => {
    const lines = parseDiff(DIFF)
    expect(commentTarget(lines.find((l) => l.kind === 'add')!)).toEqual({ line: 2, side: 'new' })
    expect(commentTarget(lines.find((l) => l.kind === 'del')!)).toEqual({ line: 2, side: 'old' })
    expect(commentTarget(lines.find((l) => l.kind === 'hunk')!)).toBeNull()
  })

  it('同一行再写一次是改掉原来的，写空了是删掉', () => {
    let list = putComment([], { file: 'a.py', line: 3, side: 'new', text: ' 改成 2 ' })
    expect(list).toEqual([{ file: 'a.py', line: 3, side: 'new', text: '改成 2' }])
    list = putComment(list, { file: 'a.py', line: 3, side: 'old', text: '这行别删' })
    list = putComment(list, { file: 'a.py', line: 3, side: 'new', text: '改成 3' })
    expect(list.map((c) => c.text)).toEqual(['这行别删', '改成 3'])
    expect(putComment(list, { file: 'a.py', line: 3, side: 'new', text: '  ' })).toHaveLength(1)
  })

  it('概况：文件数、加删行数、测试和 CI', () => {
    const c = card({
      changes: { files: [{ path: 'a', added: 3, deleted: 1, diff: '', untracked: false }, { path: 'b', added: null, deleted: null, diff: '', untracked: false }], base: 'HEAD', note: '' },
      tests: { command: 'pytest', ok: false, tail: '' },
      ci: { state: 'pending', checks: [], source: 'api' }
    })
    expect(brief(c)).toEqual({ files: 2, added: 3, deleted: 1, tests: 'fail', ci: 'pending' })
    expect(hasDelivery(c)).toBe(true)
    expect(hasDelivery(card())).toBe(false)
    expect(hasDelivery(null)).toBe(false)
  })

  it('主进程只转发像样的验收', () => {
    const ok = { file: 'a.py', line: 1, side: 'new', text: '改一下' }
    expect(validAcceptance('accepted', '', [])).toBe(true)
    expect(validAcceptance('rejected', '总的意见', [ok])).toBe(true)
    expect(validAcceptance('maybe', '', [])).toBe(false)
    expect(validAcceptance('accepted', 1, [])).toBe(false)
    expect(validAcceptance('accepted', '', 'x')).toBe(false)
    for (const bad of [{ ...ok, line: 0 }, { ...ok, line: 1.5 }, { ...ok, side: 'x' }, { ...ok, text: ' ' }, { ...ok, file: '' }, null]) {
      expect(validAcceptance('rejected', '', [bad])).toBe(false)
    }
  })
})
