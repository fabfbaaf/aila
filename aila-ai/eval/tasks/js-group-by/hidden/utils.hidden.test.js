const test = require('node:test')
const assert = require('node:assert')
const { groupBy } = require('./utils.js')

test('groupBy 用函数、空列表', () => {
  assert.deepStrictEqual(groupBy([1, 2, 3, 4, 5], (n) => (n % 2 ? '单' : '双')), { 单: [1, 3, 5], 双: [2, 4] })
  assert.deepStrictEqual(groupBy([], 'x'), {})
})
