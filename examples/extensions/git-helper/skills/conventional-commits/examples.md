# 例子

```
feat(cli): 加 --json 输出，方便放进脚本
```

```
fix: 登录令牌过期后同步连接不再一直开着

握手后连接不检查令牌；现在下一次心跳时发现过期就断开，客户端拿新令牌重连。
```

```
refactor!: 配置文件从 config.ini 换成 config.toml

BREAKING CHANGE: 旧的 config.ini 不再读取，运行 tools/migrate-config 转换一次。
```
