import { describe, expect, it } from 'vitest'
import type { CodeGraph, GraphNode } from '../../../shared/workbench'
import { COLD_COLOR, TESTED_COLOR, TEST_FILE_COLOR, UNTESTED_COLOR, churnColor, crossLinks, hottest, importsOf, modeColors, testedFiles, testedStem, withoutTests } from './layout'

const node = (id: string, extra: Partial<GraphNode> = {}): GraphNode => ({
  id,
  name: id.split('/').pop()!,
  group: 'ai',
  lang: 'python',
  lines: 50,
  functions: 2,
  modified: false,
  ...extra
})

const GRAPH: CodeGraph = {
  nodes: [
    node('app/cart.py', { churn: 12 }),
    node('app/orders.py', { churn: 1 }),
    node('app/util.py'),
    node('tests/test_cart.py', { test: true }),
    node('tests/test_misc.py', { test: true, churn: 3 })
  ],
  links: [{ source: 'tests/test_misc.py', target: 'app/orders.py', weight: 2 }]
}

describe('换颜色看项目', () => {
  it('改动越多越热；没改过是暗色', () => {
    expect(churnColor(0, 10)).toBe(COLD_COLOR)
    const [low, high] = [churnColor(1, 12), churnColor(12, 12)]
    expect(low).not.toBe(high)
    const [r, g, b] = [1, 3, 5].map((i) => parseInt(high.slice(i, i + 2), 16))
    expect(r).toBeGreaterThan(200) // 最热是红
    expect(r).toBeGreaterThan(g + 100)
    expect(r).toBeGreaterThan(b + 100)
    expect(hottest(GRAPH).map((n) => n.name)).toEqual(['cart.py', 'test_misc.py', 'orders.py'])
  })

  it('测试文件对应的文件名', () => {
    expect(testedStem('test_cart.py')).toBe('cart')
    expect(testedStem('cart_test.py')).toBe('cart')
    expect(testedStem('fold.test.ts')).toBe('fold')
    expect(testedStem('CartServiceTest.java')).toBe('cartservice')
    expect(testedStem('cart.py')).toBeNull()
  })

  it('有测试：测试文件调用了它，或者有同名的测试文件', () => {
    expect(testedFiles(GRAPH)).toEqual(new Set(['app/cart.py', 'app/orders.py']))
    const c = modeColors(GRAPH, 'tests')!
    expect(c.files.get('app/cart.py')).toBe(TESTED_COLOR)
    expect(c.files.get('app/util.py')).toBe(UNTESTED_COLOR)
    expect(c.files.get('tests/test_cart.py')).toBe(TEST_FILE_COLOR)
    expect(c.modules.get('tests')).toBe(TEST_FILE_COLOR)
    expect(c.modules.get('app')).toMatch(/^#[0-9a-f]{6}$/)
    expect(modeColors(GRAPH, 'group')).toBeNull()
    expect(modeColors(GRAPH, 'churn')!.files.get('app/util.py')).toBe(COLD_COLOR)
  })
})

describe('import 和第三方库', () => {
  const g: CodeGraph = {
    nodes: [node('app/a.py'), node('app/b.py'), node('tests/test_a.py', { test: true })],
    links: [
      { source: 'app/a.py', target: 'app/b.py', weight: 1, kind: 'import' },
      { source: 'tests/test_a.py', target: 'app/a.py', weight: 1, kind: 'import' },
      { source: 'app/a.py', target: 'app/b.py', weight: 2, kind: 'http', via: ['/x'] }
    ],
    libs: [
      { id: 'python:fastapi', name: 'fastapi', ecosystem: 'python', users: ['app/a.py'], count: 1 },
      { id: 'python:pytest', name: 'pytest', ecosystem: 'python', users: ['tests/test_a.py'], count: 1 }
    ]
  }

  it('选中文件：引用了谁、谁引用了它、用到的库；跨进程的光线不算 import', () => {
    expect(importsOf(g, 'app/a.py')).toEqual({ out: ['app/b.py'], in: ['tests/test_a.py'], libs: ['fastapi'] })
    expect(crossLinks(g, 'app/a.py').out.map((l) => l.kind)).toEqual(['http'])
  })

  it('隐藏测试文件：只有测试在用的库也一起藏起来', () => {
    const shown = withoutTests(g)
    expect(shown.libs?.map((l) => l.name)).toEqual(['fastapi'])
    expect(shown.links.some((l) => l.source.startsWith('tests/'))).toBe(false)
  })
})
