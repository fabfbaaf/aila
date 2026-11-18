// 商品按价格排序

function sortByPrice(products) {
  return products.sort((a, b) => a.price > b.price)
}

module.exports = { sortByPrice }
