-- 艾拉云端：账号、登录令牌、对话消息、设置

-- 用户。last_seq 是这个用户最近一次变更的序号：每次写消息、改设置都 +1，
-- 客户端记住自己同步到哪个序号，下次只拉更大的
create table users (
    id            bigint generated always as identity primary key,
    email         varchar(254) not null unique,
    password_hash varchar(100) not null,
    display_name  varchar(50)  not null,
    last_seq      bigint       not null default 0,
    created_at    timestamptz  not null default now()
);

-- 刷新令牌：数据库里只存哈希，泄露了也用不了。
-- 每次刷新都换一个新的（旧的标记 used_at），同一次登录换出来的令牌共用一个 family_id；
-- 已经用过的令牌又被拿来刷新，说明被偷了，整个 family 作废
create table refresh_tokens (
    id          bigint generated always as identity primary key,
    user_id     bigint       not null references users (id) on delete cascade,
    token_hash  varchar(64)  not null unique,
    family_id   uuid         not null,
    device_id   varchar(64)  not null,
    device_name varchar(100) not null,
    created_at  timestamptz  not null default now(),
    expires_at  timestamptz  not null,
    used_at     timestamptz,
    revoked_at  timestamptz
);
create index refresh_tokens_family on refresh_tokens (family_id);
create index refresh_tokens_user on refresh_tokens (user_id);

-- 对话消息。id 由客户端生成（UUID）：断网重传、重复上传同一条都不会多出一条。
-- 删除只做标记（墓碑），这样别的设备同步时也能知道要删
create table messages (
    id         uuid        primary key,
    user_id    bigint      not null references users (id) on delete cascade,
    role       varchar(16) not null check (role in ('user', 'assistant')),
    content    text        not null,
    created_at timestamptz not null,
    device_id  varchar(64) not null,
    deleted    boolean     not null default false,
    seq        bigint      not null
);
create unique index messages_user_seq on messages (user_id, seq);

-- 设置：每个键只留最新值，值是 JSON
create table settings (
    user_id    bigint      not null references users (id) on delete cascade,
    key        varchar(64) not null,
    value      text        not null,
    deleted    boolean     not null default false,
    seq        bigint      not null,
    updated_at timestamptz not null default now(),
    primary key (user_id, key)
);
create unique index settings_user_seq on settings (user_id, seq);
