package com.aila.server.sync;

import static org.assertj.core.api.Assertions.assertThat;

import java.time.Instant;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.stream.IntStream;
import java.util.stream.LongStream;

import org.junit.jupiter.api.Test;

import com.aila.server.IntegrationTest;

import tools.jackson.databind.JsonNode;

class SyncIntegrationTest extends IntegrationTest {

    private static Map<String, Object> message(UUID id, String role, String content) {
        return Map.of("id", id.toString(), "role", role, "content", content, "createdAt", Instant.now().toString());
    }

    private static List<Map<String, Object>> messages(int n) {
        return IntStream.range(0, n).mapToObj(i -> message(UUID.randomUUID(), i % 2 == 0 ? "user" : "assistant", "第 " + i + " 句")).toList();
    }

    private JsonNode upload(Session s, List<Map<String, Object>> batch) {
        var res = post("/api/messages", s.accessToken(), Map.of("messages", batch));
        assertThat(res.getStatusCode().value()).isEqualTo(200);
        return res.getBody();
    }

    private JsonNode pull(Session s, long since, int limit) {
        var res = get("/api/sync?since=" + since + "&limit=" + limit, s.accessToken());
        assertThat(res.getStatusCode().value()).isEqualTo(200);
        return res.getBody();
    }

    private static List<Long> seqs(JsonNode changes) {
        return changes.valueStream().map(c -> c.get("seq").asLong()).toList();
    }

    @Test
    void uploadThenPullInOrder() {
        var s = register();
        var res = upload(s, messages(3));
        assertThat(res.get("accepted").valueStream().map(a -> a.get("seq").asLong())).containsExactly(1L, 2L, 3L);
        assertThat(res.get("lastSeq").asLong()).isEqualTo(3);

        var all = pull(s, 0, 100);
        assertThat(seqs(all.get("changes"))).containsExactly(1L, 2L, 3L);
        var first = all.get("changes").get(0);
        assertThat(first.get("type").asString()).isEqualTo("message");
        assertThat(first.get("data").get("content").asString()).isEqualTo("第 0 句");
        assertThat(first.get("data").get("deviceId").asString()).isEqualTo("pc-1"); // 来自令牌，不是客户端自己说的
        assertThat(all.get("hasMore").asBoolean()).isFalse();

        assertThat(seqs(pull(s, 2, 100).get("changes"))).containsExactly(3L);
        assertThat(pull(s, 3, 100).get("changes").size()).isZero();
    }

    @Test
    void retryingTheSameUploadDoesNotDuplicate() {
        var s = register();
        var batch = messages(3);
        var first = upload(s, batch);
        var again = upload(s, batch); // 网络超时后客户端重传
        assertThat(again.get("accepted")).isEqualTo(first.get("accepted"));
        assertThat(again.get("lastSeq").asLong()).isEqualTo(3);

        // 一批里面有重复的 id 也只存一份
        var id = UUID.randomUUID();
        var dup = upload(s, List.of(message(id, "user", "a"), message(id, "user", "a")));
        assertThat(dup.get("accepted").size()).isEqualTo(1);
        assertThat(pull(s, 0, 100).get("changes").size()).isEqualTo(4);
    }

    @Test
    void pagingDoesNotSkipOrRepeat() {
        var s = register();
        upload(s, messages(7));
        var seen = new ArrayList<Long>();
        long since = 0;
        int pages = 0;
        JsonNode page;
        do {
            page = pull(s, since, 3);
            seen.addAll(seqs(page.get("changes")));
            since = page.get("nextSince").asLong();
            pages++;
        } while (page.get("hasMore").asBoolean());
        assertThat(seen).containsExactly(1L, 2L, 3L, 4L, 5L, 6L, 7L);
        assertThat(pages).isEqualTo(3);
    }

    @Test
    void deletionIsSyncedAsATombstone() {
        var s = register();
        var id = UUID.randomUUID();
        upload(s, List.of(message(id, "user", "不想留下的话")));

        var res = http().delete().uri("/api/messages/" + id).header("Authorization", "Bearer " + s.accessToken())
                .retrieve().toEntity(JsonNode.class);
        assertThat(res.getBody().get("seq").asLong()).isEqualTo(2);

        // 别的设备从序号 1 之后拉：拿到删除标记，内容已经清空
        var changes = pull(s, 1, 100).get("changes");
        assertThat(changes.size()).isEqualTo(1);
        assertThat(changes.get(0).get("data").get("deleted").asBoolean()).isTrue();
        assertThat(changes.get(0).get("data").get("content").asString()).isEmpty();
        // 从头拉的设备也只看到最新状态，不会先看到内容再看到删除
        assertThat(pull(s, 0, 100).get("changes").size()).isEqualTo(1);
    }

    @Test
    void settingsKeepOnlyTheLatestValue() {
        var s = register();
        put(s, "voice.speak", Map.of("value", false));
        put(s, "voice.speak", Map.of("value", true));
        put(s, "persona.name", Map.of("value", Map.of("nick", "艾拉")));

        var changes = pull(s, 0, 100).get("changes");
        assertThat(seqs(changes)).containsExactly(2L, 3L);
        assertThat(changes.get(0).get("data").get("key").asString()).isEqualTo("voice.speak");
        assertThat(changes.get(0).get("data").get("value").asBoolean()).isTrue();
        assertThat(changes.get(1).get("data").get("value").get("nick").asString()).isEqualTo("艾拉");

        var deleted = http().delete().uri("/api/settings/voice.speak").header("Authorization", "Bearer " + s.accessToken())
                .retrieve().toEntity(JsonNode.class);
        assertThat(deleted.getBody().get("seq").asLong()).isEqualTo(4);
        assertThat(pull(s, 3, 100).get("changes").get(0).get("data").get("deleted").asBoolean()).isTrue();

        assertThat(putStatus(s, "Bad Key!", Map.of("value", 1))).isEqualTo(400);
        assertThat(putStatus(s, "big", Map.of("value", "x".repeat(5000)))).isEqualTo(400);
    }

    @Test
    void usersCannotSeeOrTouchEachOthersData() {
        var alice = register();
        var bob = register();
        var id = UUID.randomUUID();
        upload(alice, List.of(message(id, "user", "爱丽丝的秘密")));

        assertThat(pull(bob, 0, 100).get("changes").size()).isZero();
        var delete = http().delete().uri("/api/messages/" + id).header("Authorization", "Bearer " + bob.accessToken())
                .retrieve().toEntity(JsonNode.class);
        assertThat(delete.getStatusCode().value()).isEqualTo(404);
        var steal = post("/api/messages", bob.accessToken(), Map.of("messages", List.of(message(id, "user", "覆盖"))));
        assertThat(steal.getStatusCode().value()).isEqualTo(409);
        assertThat(pull(alice, 0, 100).get("changes").get(0).get("data").get("content").asString()).isEqualTo("爱丽丝的秘密");
    }

    @Test
    void concurrentWritesGetUniqueGaplessSequenceNumbers() throws Exception {
        var s = register();
        int threads = 8;
        int perThread = 25;
        var seqs = Collections.synchronizedList(new ArrayList<Long>());
        try (var pool = Executors.newFixedThreadPool(threads)) {
            var futures = new ArrayList<Future<?>>();
            for (int t = 0; t < threads; t++) {
                futures.add(pool.submit(() -> {
                    for (int i = 0; i < perThread; i++) {
                        upload(s, messages(1)).get("accepted").valueStream().forEach(a -> seqs.add(a.get("seq").asLong()));
                    }
                }));
            }
            for (var f : futures) {
                f.get();
            }
        }
        long total = (long) threads * perThread;
        assertThat(seqs).hasSize((int) total).doesNotHaveDuplicates();
        assertThat(seqs).containsExactlyInAnyOrderElementsOf(LongStream.rangeClosed(1, total).boxed().toList());
        assertThat(pull(s, 0, 500).get("lastSeq").asLong()).isEqualTo(total);
    }

    @Test
    void rejectsBadInput() {
        var s = register();
        var badRole = post("/api/messages", s.accessToken(), Map.of("messages", List.of(message(UUID.randomUUID(), "system", "x"))));
        assertThat(badRole.getStatusCode().value()).isEqualTo(400);
        var empty = post("/api/messages", s.accessToken(), Map.of("messages", List.of()));
        assertThat(empty.getStatusCode().value()).isEqualTo(400);
        var tooMany = post("/api/messages", s.accessToken(), Map.of("messages", messages(101)));
        assertThat(tooMany.getStatusCode().value()).isEqualTo(400);
    }

    private void put(Session s, String key, Object body) {
        assertThat(putStatus(s, key, body)).isEqualTo(200);
    }

    private int putStatus(Session s, String key, Object body) {
        return http().put().uri("/api/settings/{key}", key).header("Authorization", "Bearer " + s.accessToken())
                .body(body).retrieve().toBodilessEntity().getStatusCode().value();
    }
}
