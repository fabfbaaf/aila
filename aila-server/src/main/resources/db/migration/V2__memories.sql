-- 长期记忆：艾拉记得的关于用户的事，一条一句话。
-- 由本地 AI 服务从对话里提取，用户可以查看和删除；和消息一样按序号在设备之间同步。
-- 和消息不同的是记忆可以修改（比如「用户住在北京」改成「用户搬到了上海」），修改也分配新序号。
create table memories (
    id         uuid         primary key,
    user_id    bigint       not null references users (id) on delete cascade,
    content    varchar(500) not null,
    -- profile 身份和经历；preference 喜好和习惯；event 有时间的事（面试、约会）；other 其他
    kind       varchar(16)  not null check (kind in ('profile', 'preference', 'event', 'other')),
    -- 有时效的记忆（「下周三面试」）过了这个时间就不再拿出来用
    expires_at timestamptz,
    created_at timestamptz  not null,
    updated_at timestamptz  not null default now(),
    deleted    boolean      not null default false,
    seq        bigint       not null
);
create unique index memories_user_seq on memories (user_id, seq);
