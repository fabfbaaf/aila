# LRU 缓存

`LRUCache(capacity)`：capacity 小于 1 抛 ValueError。

- `put(key, value)`：放进去；key 已经有了就更新值。放满时先淘汰最久没用过的。
- `get(key, default=None)`：取值，没有返回 default。取到也算「用过」。
- `len(cache)`：现在有几个。
- put 已有的 key 也算「用过」。
