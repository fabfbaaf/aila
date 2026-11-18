const test = require('node:test')
const assert = require('node:assert')
const { chunk, groupBy } = require('./utils.js')

test('chunk', () => {
  assert.deepStrictEqual(chunk([1, 2, 3], 2), [[1, 2], [3]])
})

test('groupBy 按属性名', () => {
  const list = [{ t: 'a', v: 1 }, { t: 'b', v: 2 }, { t: 'a', v: 3 }]
  assert.deepStrictEqual(groupBy(list, 't'), { a: [list[0], list[2]], b: [list[1]] })
})
