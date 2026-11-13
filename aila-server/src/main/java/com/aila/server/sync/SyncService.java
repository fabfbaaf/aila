package com.aila.server.sync;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.UUID;
import java.util.regex.Pattern;

import org.springframework.context.ApplicationEventPublisher;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;

import com.aila.server.common.ApiException;
import com.aila.server.sync.SyncDtos.Accepted;
import com.aila.server.sync.SyncDtos.Change;
import com.aila.server.sync.SyncDtos.MemoryData;
import com.aila.server.sync.SyncDtos.MemoryRequest;
import com.aila.server.sync.SyncDtos.MessageData;
import com.aila.server.sync.SyncDtos.NewMessage;
import com.aila.server.sync.SyncDtos.SettingData;
import com.aila.server.sync.SyncDtos.SyncResponse;
import com.aila.server.sync.SyncDtos.UploadResponse;

import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/**
 * 多设备同步的核心：每个用户的每次变更都有一个递增的序号（seq），客户端记住同步到了哪个序号，
 * 下次只拉更大的。
 *
 * 为什么不会漏：写入前先 SELECT ... FOR UPDATE 锁住这个用户的那一行，再分配序号，锁到提交才释放。
 * 所以同一个用户的写入是排队进行的，序号小的一定先提交。读的一方看到了序号 N，
 * 就不可能还有一个比 N 小的序号「还没提交、之后才冒出来」。
 * 如果改用全局自增序列，两个事务可能 105 先提交、104 后提交，按 seq > 104 拉的客户端就永远错过 104。
 *
 * 不同用户之间互不影响，锁的粒度是一个用户。
 */
@Service
public class SyncService {

    private static final Pattern SETTING_KEY = Pattern.compile("[a-z0-9][a-z0-9._-]{0,63}");
    private static final int MAX_SETTING_CHARS = 4096;
    private static final int MAX_PAGE = 500;

    private final JdbcClient jdbc;
    private final ApplicationEventPublisher events;
    private final JsonMapper json;

    public SyncService(JdbcClient jdbc, ApplicationEventPublisher events, JsonMapper json) {
        this.jdbc = jdbc;
        this.events = events;
        this.json = json;
    }

    // ---------- 写 ----------

    @Transactional
    public UploadResponse saveMessages(long userId, String deviceId, List<NewMessage> messages) {
        long base = lockUser(userId);
        var unique = new LinkedHashMap<UUID, NewMessage>();
        messages.forEach(m -> unique.putIfAbsent(m.id(), m));

        // 已经存过的（客户端重传）直接返回原来的序号
        var seqs = new HashMap<UUID, Long>();
        jdbc.sql("select id, user_id, seq from messages where id in (:ids)")
                .param("ids", unique.keySet())
                .query((rs, i) -> {
                    if (rs.getLong("user_id") != userId) {
                        throw new ApiException(HttpStatus.CONFLICT, "ID_CONFLICT", "消息 id 冲突");
                    }
                    return seqs.put(rs.getObject("id", UUID.class), rs.getLong("seq"));
                })
                .list();

        long seq = base;
        for (var m : unique.values()) {
            if (seqs.containsKey(m.id())) {
                continue;
            }
            seq++;
            jdbc.sql("""
                    insert into messages (id, user_id, role, content, created_at, device_id, seq)
                    values (:id, :userId, :role, :content, :createdAt, :deviceId, :seq)
                    """)
                    .param("id", m.id())
                    .param("userId", userId)
                    .param("role", m.role())
                    .param("content", m.content())
                    .param("createdAt", utc(m.createdAt()))
                    .param("deviceId", deviceId)
                    .param("seq", seq)
                    .update();
            seqs.put(m.id(), seq);
        }
        if (seq > base) {
            advance(userId, seq);
        }
        var accepted = unique.keySet().stream().map(id -> new Accepted(id, seqs.get(id))).toList();
        return new UploadResponse(accepted, seq);
    }

    /** 删除只打标记、清空内容，并分配新序号：别的设备拉到这条变更后也会删掉 */
    @Transactional
    public long deleteMessage(long userId, UUID id) {
        long base = lockUser(userId);
        var row = jdbc.sql("select user_id, deleted, seq from messages where id = :id")
                .param("id", id)
                .query((rs, i) -> new long[] { rs.getLong("user_id"), rs.getBoolean("deleted") ? 1 : 0, rs.getLong("seq") })
                .optional()
                .filter(r -> r[0] == userId)
                .orElseThrow(() -> new ApiException(HttpStatus.NOT_FOUND, "NOT_FOUND", "没有这条消息"));
        if (row[1] == 1) {
            return row[2]; // 已经删过了
        }
        long seq = base + 1;
        jdbc.sql("update messages set deleted = true, content = '', seq = :seq where id = :id")
                .param("seq", seq)
                .param("id", id)
                .update();
        advance(userId, seq);
        return seq;
    }

    /**
     * 新增或修改一条记忆。已经删除的记忆不能再改（返回 410）：
     * 用户在一台电脑上让她忘掉的事，不能被另一台电脑离线时做的修改「复活」。删除优先。
     */
    @Transactional
    public long putMemory(long userId, UUID id, MemoryRequest m) {
        long seq = lockUser(userId) + 1;
        var existing = jdbc.sql("select user_id, deleted from memories where id = :id")
                .param("id", id)
                .query((rs, i) -> new long[] { rs.getLong("user_id"), rs.getBoolean("deleted") ? 1 : 0 })
                .optional();
        if (existing.isPresent()) {
            if (existing.get()[0] != userId) {
                throw new ApiException(HttpStatus.CONFLICT, "ID_CONFLICT", "记忆 id 冲突");
            }
            if (existing.get()[1] == 1) {
                throw new ApiException(HttpStatus.GONE, "MEMORY_DELETED", "这条记忆已经删掉了");
            }
        }
        jdbc.sql("""
                insert into memories (id, user_id, content, kind, expires_at, created_at, seq)
                values (:id, :userId, :content, :kind, :expiresAt, :createdAt, :seq)
                on conflict (id) do update
                    set content = excluded.content, kind = excluded.kind, expires_at = excluded.expires_at,
                        updated_at = now(), seq = excluded.seq
                """)
                .param("id", id)
                .param("userId", userId)
                .param("content", m.content().strip())
                .param("kind", m.kind())
                .param("expiresAt", m.expiresAt() == null ? null : utc(m.expiresAt()))
                .param("createdAt", utc(m.createdAt()))
                .param("seq", seq)
                .update();
        advance(userId, seq);
        return seq;
    }

    @Transactional
    public long deleteMemory(long userId, UUID id) {
        long base = lockUser(userId);
        var row = jdbc.sql("select user_id, deleted, seq from memories where id = :id")
                .param("id", id)
                .query((rs, i) -> new long[] { rs.getLong("user_id"), rs.getBoolean("deleted") ? 1 : 0, rs.getLong("seq") })
                .optional()
                .filter(r -> r[0] == userId)
                .orElseThrow(() -> new ApiException(HttpStatus.NOT_FOUND, "NOT_FOUND", "没有这条记忆"));
        if (row[1] == 1) {
            return row[2];
        }
        long seq = base + 1;
        jdbc.sql("update memories set deleted = true, content = '', updated_at = now(), seq = :seq where id = :id")
                .param("seq", seq)
                .param("id", id)
                .update();
        advance(userId, seq);
        return seq;
    }

    @Transactional
    public long putSetting(long userId, String key, JsonNode value) {
        checkKey(key);
        var text = value.toString();
        if (text.length() > MAX_SETTING_CHARS) {
            throw new ApiException(HttpStatus.BAD_REQUEST, "VALIDATION", "设置的值太大了");
        }
        return writeSetting(userId, key, text, false);
    }

    @Transactional
    public long deleteSetting(long userId, String key) {
        checkKey(key);
        return writeSetting(userId, key, "null", true);
    }

    private long writeSetting(long userId, String key, String value, boolean deleted) {
        long seq = lockUser(userId) + 1;
        jdbc.sql("""
                insert into settings (user_id, key, value, deleted, seq) values (:userId, :key, :value, :deleted, :seq)
                on conflict (user_id, key) do update
                    set value = excluded.value, deleted = excluded.deleted, seq = excluded.seq, updated_at = now()
                """)
                .param("userId", userId)
                .param("key", key)
                .param("value", value)
                .param("deleted", deleted)
                .param("seq", seq)
                .update();
        advance(userId, seq);
        return seq;
    }

    /** 锁住这个用户，返回当前序号。同一个用户的所有写入从这里开始排队 */
    private long lockUser(long userId) {
        return jdbc.sql("select last_seq from users where id = :id for update")
                .param("id", userId)
                .query(Long.class)
                .optional()
                .orElseThrow(() -> new ApiException(HttpStatus.UNAUTHORIZED, "USER_GONE", "账号不存在"));
    }

    private void advance(long userId, long seq) {
        jdbc.sql("update users set last_seq = :seq where id = :id").param("seq", seq).param("id", userId).update();
        events.publishEvent(new ChangesCommitted(userId, seq));
    }

    // ---------- 读 ----------

    /**
     * 拉取 since 之后的变更，按序号从小到大。可重复读隔离级别：
     * 同一次拉取里的几条查询看到的是同一个时间点的数据。
     */
    @Transactional(readOnly = true, isolation = Isolation.REPEATABLE_READ)
    public SyncResponse changes(long userId, long since, int limit) {
        int n = Math.clamp(limit, 1, MAX_PAGE);
        long lastSeq = jdbc.sql("select last_seq from users where id = :id").param("id", userId).query(Long.class).single();

        var all = new ArrayList<Change>();
        // 每张表多取一条，用来判断是不是还有下一页
        all.addAll(jdbc.sql("""
                select id, role, content, created_at, device_id, deleted, seq from messages
                where user_id = :userId and seq > :since order by seq limit :n
                """)
                .param("userId", userId).param("since", since).param("n", n + 1)
                .query((rs, i) -> new Change(rs.getLong("seq"), "message", message(rs)))
                .list());
        all.addAll(jdbc.sql("""
                select key, value, deleted, seq from settings
                where user_id = :userId and seq > :since order by seq limit :n
                """)
                .param("userId", userId).param("since", since).param("n", n + 1)
                .query((rs, i) -> new Change(rs.getLong("seq"), "setting",
                        new SettingData(rs.getString("key"), json.readTree(rs.getString("value")), rs.getBoolean("deleted"))))
                .list());
        all.addAll(jdbc.sql("""
                select id, content, kind, expires_at, created_at, updated_at, deleted, seq from memories
                where user_id = :userId and seq > :since order by seq limit :n
                """)
                .param("userId", userId).param("since", since).param("n", n + 1)
                .query((rs, i) -> new Change(rs.getLong("seq"), "memory", memory(rs)))
                .list());

        all.sort(Comparator.comparingLong(Change::seq));
        boolean hasMore = all.size() > n;
        var page = hasMore ? all.subList(0, n) : all;
        long nextSince = page.isEmpty() ? since : page.getLast().seq();
        return new SyncResponse(List.copyOf(page), nextSince, hasMore, lastSeq);
    }

    private static MessageData message(ResultSet rs) throws SQLException {
        return new MessageData(
                rs.getObject("id", UUID.class),
                rs.getString("role"),
                rs.getString("content"),
                rs.getObject("created_at", OffsetDateTime.class).toInstant(),
                rs.getString("device_id"),
                rs.getBoolean("deleted"));
    }

    private static MemoryData memory(ResultSet rs) throws SQLException {
        var expires = rs.getObject("expires_at", OffsetDateTime.class);
        return new MemoryData(
                rs.getObject("id", UUID.class),
                rs.getString("content"),
                rs.getString("kind"),
                expires == null ? null : expires.toInstant(),
                rs.getObject("created_at", OffsetDateTime.class).toInstant(),
                rs.getObject("updated_at", OffsetDateTime.class).toInstant(),
                rs.getBoolean("deleted"));
    }

    private static OffsetDateTime utc(Instant t) {
        return t.atOffset(ZoneOffset.UTC);
    }

    private static void checkKey(String key) {
        if (!SETTING_KEY.matcher(key).matches()) {
            throw new ApiException(HttpStatus.BAD_REQUEST, "VALIDATION", "设置的键只能用小写字母、数字和 . _ -");
        }
    }
}
