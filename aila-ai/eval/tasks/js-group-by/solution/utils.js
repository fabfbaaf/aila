// 小工具

function chunk(list, size) {
  const out = []
  for (let i = 0; i < list.length; i += size) out.push(list.slice(i, i + size))
  return out
}

function groupBy(list, key) {
  const pick = typeof key === 'function' ? key : (item) => item[key]
  const out = {}
  for (const item of list) {
    const k = pick(item)
    ;(out[k] ??= []).push(item)
  }
  return out
}

module.exports = { chunk, groupBy }
