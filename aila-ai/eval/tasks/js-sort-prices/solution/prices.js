// 商品按价格排序

function sortByPrice(products) {
  return [...products].sort((a, b) => Number(a.price) - Number(b.price) || (a.name < b.name ? -1 : a.name > b.name ? 1 : 0))
}

module.exports = { sortByPrice }
