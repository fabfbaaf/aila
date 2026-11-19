# 部署云端同步服务

艾拉本身不需要云端：不登录的话，对话、记忆、设置都只在本机。要在几台电脑之间同步，才需要把 `aila-server` 跑在一台大家都连得到的机器上。

这里是用 Docker 一条命令拉起来的配置：同步服务 + PostgreSQL，可选用 Caddy 自动配 HTTPS。

## 准备

- 一台装了 Docker（含 Compose）的机器：云服务器、家里常开的电脑、NAS 都行
- 把整个仓库拷过去（或者 `git clone`），进到 `deploy/server/`
- `cp .env.example .env`，填两项必填的：
  - `AILA_DB_PASSWORD`：数据库密码，`openssl rand -hex 24`
  - `AILA_JWT_SECRET`：访问令牌的签名密钥，`openssl rand -base64 48`（换了它所有设备都要重新登录）

## 三种用法

### 1. 有域名：公网 + HTTPS（推荐）

域名解析到这台机器、80 和 443 端口能从外网访问。`.env` 里：

```
AILA_DOMAIN=sync.example.com
AILA_BIND=127.0.0.1
AILA_FORWARD_HEADERS=native
```

```
docker compose --profile https up -d --build
```

Caddy 会自动申请、续期证书。桌面端登录时服务器地址填 `https://sync.example.com`（同步的 WebSocket 自动走 `wss://`）。

### 2. 局域网或 ZeroTier / Tailscale 里

只在家里几台电脑之间同步，或者用 ZeroTier、Tailscale 这类组网（它们自己加密）：`.env` 里 `AILA_BIND=0.0.0.0`，`AILA_FORWARD_HEADERS=none`，

```
docker compose up -d --build
```

桌面端填 `http://<这台机器的局域网或 ZeroTier 地址>:8790`。
**不要这样直接暴露到公网**：没有 HTTPS 的话，密码和令牌是明文传的。

### 3. 只在这台电脑上试试

不改 `AILA_BIND`（默认 `127.0.0.1`），`docker compose up -d --build`，桌面端用默认的 `http://127.0.0.1:8790`。
（不想装 Docker 的话，`aila-server` 里 `.\mvnw.cmd spring-boot:test-run` 会带一个内嵌的数据库，见 `aila-server/README.md`。）

## 日常

| 做什么 | 命令 |
|---|---|
| 看状态 | `docker compose ps`；`curl http://127.0.0.1:8790/actuator/health` 应该是 `{"status":"UP"}` |
| 看日志 | `docker compose logs -f server` |
| 更新 | `git pull` 以后 `docker compose up -d --build`（数据库结构由 Flyway 自动迁移） |
| 备份 | `docker compose exec db pg_dump -U aila aila > aila-backup.sql` |
| 恢复 | `docker compose exec -T db psql -U aila aila < aila-backup.sql` |
| 停 | `docker compose down`（数据在 `pgdata` 卷里，不会丢；`down -v` 才会删） |

## 安全上要知道的

- 数据库不对外开端口，只有同步服务连得到
- 同步服务默认只在 `127.0.0.1` 听，要别的电脑连得改 `AILA_BIND`；对公网一定走 HTTPS（用法 1）
- `AILA_FORWARD_HEADERS=native` 只在 Caddy 后面用：直接对外时开了它，谁都能伪造来源 IP 绕过登录限流
- `/actuator` 只放了 `health`；登录有按「邮箱 + IP」的锁定和按 IP 的限流（见 `docs/security-review-2026-11.md` 的云端那一节）
- 限流存在内存里，只适合跑一个实例
- `.env` 里有数据库密码和签名密钥，别提交、别发给别人
