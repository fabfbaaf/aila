package com.aila.server.sync;

import static org.assertj.core.api.Assertions.assertThat;

import java.time.Instant;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

import org.junit.jupiter.api.Test;

import com.aila.server.IntegrationTest;

import tools.jackson.databind.JsonNode;

class MemorySyncTest extends IntegrationTest {

    private int put(Session s, UUID id, String content, String kind) {
        var body = new HashMap<String, Object>(Map.of("content", content, "kind", kind, "createdAt", Instant.now().toString()));
        return http().put().uri("/api/memories/" + id).header("Authorization", "Bearer " + s.accessToken())
                .body(body).retrieve().toBodilessEntity().getStatusCode().value();
    }

    private int delete(Session s, UUID id) {
        return http().delete().uri("/api/memories/" + id).header("Authorization", "Bearer " + s.accessToken())
                .retrieve().toBodilessEntity().getStatusCode().value();
    }

    private List<JsonNode> memories(Session s, long since) {
        return get("/api/sync?since=" + since, s.accessToken()).getBody().get("changes").valueStream()
                .filter(c -> c.get("type").asString().equals("memory")).toList();
    }

    @Test
    void addThenUpdateKeepsOnlyTheLatestVersion() {
        var s = register();
        var id = UUID.randomUUID();
        assertThat(put(s, id, "用户住在北京", "profile")).isEqualTo(200);
        assertThat(put(s, id, "用户搬到了上海", "profile")).isEqualTo(200);

        var all = memories(s, 0);
        assertThat(all).hasSize(1);
        assertThat(all.get(0).get("seq").asLong()).isEqualTo(2);
        assertThat(all.get(0).get("data").get("content").asString()).isEqualTo("用户搬到了上海");
        assertThat(all.get(0).get("data").get("kind").asString()).isEqualTo("profile");
    }

    @Test
    void deletedMemoriesCannotBeResurrected() {
        var s = register();
        var id = UUID.randomUUID();
        put(s, id, "用户下周三有一场面试", "event");
        assertThat(delete(s, id)).isEqualTo(200);

        // 另一台电脑离线时改过这条，现在才传上来：删除优先
        assertThat(put(s, id, "用户下周四有一场面试", "event")).isEqualTo(410);
        var tombstone = memories(s, 1);
        assertThat(tombstone).hasSize(1);
        assertThat(tombstone.get(0).get("data").get("deleted").asBoolean()).isTrue();
        assertThat(tombstone.get(0).get("data").get("content").asString()).isEmpty();
    }

    @Test
    void memoriesAreMixedIntoTheSameSequenceAsMessages() {
        var s = register();
        post("/api/messages", s.accessToken(), Map.of("messages", List.of(Map.of(
                "id", UUID.randomUUID().toString(), "role", "user", "content", "我养了一只橘猫", "createdAt", Instant.now().toString()))));
        put(s, UUID.randomUUID(), "用户养了一只橘猫", "profile");

        var changes = get("/api/sync?since=0", s.accessToken()).getBody().get("changes");
        assertThat(changes.valueStream().map(c -> c.get("type").asString())).containsExactly("message", "memory");
        assertThat(changes.valueStream().map(c -> c.get("seq").asLong())).containsExactly(1L, 2L);
    }

    @Test
    void validationAndIsolation() {
        var alice = register();
        var bob = register();
        var id = UUID.randomUUID();
        put(alice, id, "用户喜欢喝美式", "preference");

        assertThat(put(bob, id, "覆盖", "preference")).isEqualTo(409);
        assertThat(delete(bob, id)).isEqualTo(404);
        assertThat(put(alice, UUID.randomUUID(), "x", "secret")).isEqualTo(400);
        assertThat(put(alice, UUID.randomUUID(), " ", "other")).isEqualTo(400);
        assertThat(put(alice, UUID.randomUUID(), "长".repeat(501), "other")).isEqualTo(400);
    }
}
