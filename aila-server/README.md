# aila-server：艾拉的云端后端

账号登录，对话、记忆和设置在你的几台电脑之间同步。Spring Boot 4 + Java 21 + PostgreSQL。

## 运行

不想装数据库，用内嵌的 PostgreSQL（数据在临时目录，关掉就没了）：

```powershell
cd aila-server
.\mvnw spring-boot:test-run      # 监听 8790
```

用本机的 PostgreSQL（数据长期保存）：

```powershell
& "C:\Program Files\PostgreSQL\16\bin\psql.exe" -U postgres -f scripts\create-db.sql   # 只需一次
.\mvnw spring-boot:run
```

测试：`.\mvnw test`（用内嵌的 PostgreSQL 16，不需要 Docker）

配置（环境变量）：

| 变量 | 默认 | 说明 |
|---|---|---|
| `AILA_SERVER_PORT` | 8790 | |
| `AILA_DB_URL` / `AILA_DB_USER` / `AILA_DB_PASSWORD` | 本机 `aila` 库 | |
| `AILA_JWT_SECRET` | 空 | Base64，至少 32 字节。留空时每次启动随机生成，正式部署必须配置 |

## 部署

放到一台大家都连得到的机器上（云服务器、常开的电脑）：用 `deploy/server/` 里的 Docker 配置，同步服务 + PostgreSQL，有域名时用 Caddy 自动配 HTTPS。步骤见 [deploy/server/README.md](../deploy/server/README.md)。镜像由本目录的 `Dockerfile` 构建（多阶段，运行时只有 JRE，用普通用户跑）。

## 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/auth/register` | 注册并登录，返回访问令牌和刷新令牌 |
| POST | `/api/auth/login` | 登录 |
| POST | `/api/auth/refresh` | 用刷新令牌换一对新令牌（旧的作废） |
| POST | `/api/auth/logout` | 作废这台设备的刷新令牌 |
| GET | `/api/me` | 当前账号和登录着的设备 |
| GET | `/api/sync?since=N&limit=200` | 拉取序号大于 N 的所有变更（消息和设置） |
| POST | `/api/messages` | 上传消息（一次最多 100 条），重复上传安全 |
| DELETE | `/api/messages/{id}` | 删除一条消息 |
| PUT / DELETE | `/api/memories/{id}` | 新增或修改 / 删除一条记忆；已删除的再修改返回 410 |
| PUT / DELETE | `/api/settings/{key}` | 改 / 删一个设置（值是任意 JSON） |
| WS | `/ws/sync` | 推送「有新变更了」；握手要带 `Authorization: Bearer` |

错误统一返回 `{"code": "EMAIL_TAKEN", "message": "这个邮箱已经注册过了"}`，`message` 可以直接给用户看。

## 同步是怎么做的

**每个用户一个递增的序号。** 每次写消息、删消息、改设置，这个用户的 `last_seq` 加一，变更记录上这个序号。客户端记住自己同步到了哪个序号，下次 `GET /api/sync?since=` 只拉更大的。

**为什么不会漏。** 写入时先 `SELECT last_seq FROM users WHERE id = ? FOR UPDATE` 锁住这个用户那一行，再分配序号，锁到事务提交才释放。所以同一个用户的写入是排队的，序号小的一定先提交：读的一方看到了序号 N，就不可能还有一个比 N 小的序号之后才冒出来。如果用全局自增序列，两个事务可能 105 先提交、104 后提交，按 `seq > 104` 拉的客户端就永远错过了 104。锁的粒度是一个用户，不同用户之间互不影响。测试里 8 个线程同时给同一个用户写 200 条，序号正好是 1 到 200，不重不漏。

**为什么不会重复。** 消息的 id 是客户端生成的 UUID。网络超时后客户端重传，服务端发现这个 id 已经有了，直接返回原来的序号。

**推送只是提醒。** WebSocket 只推 `{"type":"changed","seq":N}`，不推数据。客户端发现 N 比自己的大，就去拉。推送丢了也没关系：每次连上服务端都会先发 `{"type":"hello","seq":最新序号}`，客户端对一下就知道漏没漏。推送在事务提交之后才发（`@TransactionalEventListener(AFTER_COMMIT)`），避免客户端来拉的时候数据还没提交。

**删除是墓碑。** 删消息不真删，而是清空内容、打上删除标记、分配新序号，这样别的设备拉到这条变更也会删掉。

**记忆可以改，但删除优先。** 记忆（艾拉记得的关于用户的事）和消息走同一套序号，不同的是可以修改（「住在北京」改成「搬到了上海」），每次修改分配新序号。一条记忆被删掉后再来修改请求，返回 410：用户在一台电脑上让她忘掉的事，不能被另一台电脑离线时做的修改复活。

**拉取一致。** 一次拉取里的几条查询在可重复读隔离级别下执行，看到的是同一个时间点的数据；分页时每张表多取一条判断有没有下一页，不会跳过也不会重复。

## 账号安全

- 密码用 BCrypt 存。BCrypt 只看前 72 个字节，超过的直接拒绝（中文一个字 3 个字节）。
- 邮箱不存在时也算一次 BCrypt，响应时间和「密码错误」差不多，错误信息也一样，别人没法试出哪些邮箱注册过。
- 同一个 IP 对同一个邮箱 15 分钟内输错 5 次，锁定 15 分钟。按「邮箱 + IP」锁：只按邮箱锁的话，谁都能故意输错几次把别人锁在外面。
- 同一个 IP 10 分钟内最多注册、登录 30 次（`aila.auth-limit`），防撞库和批量注册。放在 nginx 这类反向代理后面时设 `AILA_FORWARD_HEADERS=native`，不然所有人都算成代理一个 IP；直接对外时别开，不然谁都能伪造 `X-Forwarded-For`。限流都存在内存里，多实例部署时换成 Redis。
- 访问令牌是 15 分钟的 JWT，服务端验签、过期时间和签发方（`aila`），不查库；刷新令牌是 30 天的随机串，数据库里只存 SHA-256。
- 刷新令牌每用一次就换新的。旧的又被拿来用，说明被偷了：这次登录换出来的整串令牌全部作废，其他设备不受影响。刷新时对令牌加行锁，同一个令牌被并发刷新也只有一个能成功；客户端也保证同一时间只发一个刷新请求。
- WebSocket 握手和普通接口一样由 Spring Security 校验令牌；不放开跨域，浏览器里的网页也带不上 Authorization 头。握手用的访问令牌过期后，下一次心跳或推送时断开（关闭码 4401），客户端拿新令牌重连：退出登录、令牌被作废的设备不会一直收着推送。
- `/actuator` 只放 `health`。`metrics` 里有接口路径、在线连接数，要接监控时用 `AILA_ACTUATOR_EXPOSE=health,metrics` 打开，并且只让内网访问。
- 每个连接包一层 `ConcurrentWebSocketSessionDecorator`：多个推送同时到达时排队发送；一个客户端卡住（发送超过 5 秒或积压超过 64KB）就断开它，不拖累别人。

## 以后要做的

- 多实例部署：WebSocket 连接分散在不同实例上，推送要经过 Redis 发布订阅；登录限流也放到 Redis。
- 压测：`aila.sync.connections`（`/actuator/metrics`，默认不对外，压测时用 `AILA_ACTUATOR_EXPOSE` 打开）是当前在线连接数，第 10 周用它测 WebSocket 并发和接口 QPS。
- 记忆的向量检索和资料库现在放在本地 AI 服务里（本机没有 pgvector，资料也不用上传）；代码索引（第 7–8 周）再考虑 pgvector。

## 目录

```
src/main/java/com/aila/server/
  auth/      注册、登录、令牌（JWT + 轮换的刷新令牌）、登录限流
  sync/      消息和设置的同步、WebSocket 推送
  config/    安全、JWT、WebSocket、配置项
  common/    统一的错误格式
src/main/resources/db/migration/   表结构（Flyway）
src/test/java/com/aila/server/
  EmbeddedPostgresConfig    内嵌 PostgreSQL
  TestAilaServerApplication spring-boot:test-run 的入口
  auth/、sync/              集成测试：真实 HTTP、真实数据库、真实 WebSocket
```
