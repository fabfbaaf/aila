# 分页

`paginate(items, page, size)`：page 从 1 开始，返回第 page 页的元素（列表）。
`page_count(items, size)`：总页数，最后不满一页的也算一页；空列表是 0 页。
page 或 size 小于 1 时抛 ValueError。运行 `python -m pytest -q` 验证。
