const test = require('node:test')
const assert = require('node:assert')
const { sortByPrice } = require('./prices.js')

test('按数字大小排，不是按字符串', () => {
  const list = [{ name: 'a', price: '10' }, { name: 'b', price: 9 }, { name: 'c', price: '12.5' }]
  assert.deepStrictEqual(sortByPrice(list).map((p) => p.name), ['b', 'a', 'c'])
})
