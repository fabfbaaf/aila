const test = require('node:test')
const assert = require('node:assert')
const { sortByPrice } = require('./prices.js')

test('不改原数组', () => {
  const list = [{ name: 'x', price: 3 }, { name: 'y', price: 1 }]
  const sorted = sortByPrice(list)
  assert.deepStrictEqual(list.map((p) => p.name), ['x', 'y'])
  assert.deepStrictEqual(sorted.map((p) => p.name), ['y', 'x'])
})

test('同价按名字', () => {
  const list = [{ name: '梨', price: 5 }, { name: 'apple', price: '5' }, { name: 'banana', price: 5 }]
  assert.deepStrictEqual(sortByPrice(list).map((p) => p.name), ['apple', 'banana', '梨'])
})
