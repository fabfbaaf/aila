package com.aila.server.sync;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.net.URI;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.TimeUnit;

import org.junit.jupiter.api.Test;
import org.springframework.web.socket.TextMessage;
import org.springframework.web.socket.WebSocketHttpHeaders;
import org.springframework.web.socket.WebSocketSession;
import org.springframework.web.socket.client.standard.StandardWebSocketClient;
import org.springframework.web.socket.handler.TextWebSocketHandler;

import com.aila.server.IntegrationTest;

class SyncSocketTest extends IntegrationTest {

    /** 收到的推送放进队列，测试里按顺序取 */
    private static final class Inbox extends TextWebSocketHandler {
        final BlockingQueue<String> messages = new LinkedBlockingQueue<>();

        @Override
        protected void handleTextMessage(WebSocketSession session, TextMessage message) {
            messages.add(message.getPayload());
        }

        String next() throws InterruptedException {
            var m = messages.poll(5, TimeUnit.SECONDS);
            assertThat(m).as("5 秒内应该收到推送").isNotNull();
            return m;
        }
    }

    private WebSocketSession connect(String token, Inbox inbox) throws Exception {
        var headers = new WebSocketHttpHeaders();
        if (token != null) {
            headers.setBearerAuth(token);
        }
        return new StandardWebSocketClient()
                .execute(inbox, headers, URI.create("ws://localhost:" + port + "/ws/sync"))
                .get(5, TimeUnit.SECONDS);
    }

    @Test
    void everyDeviceIsToldWhenSomethingChanges() throws Exception {
        var email = newEmail();
        var home = register(email, "home");
        var work = login(email, "work");
        var homeInbox = new Inbox();
        var workInbox = new Inbox();
        try (var a = connect(home.accessToken(), homeInbox); var b = connect(work.accessToken(), workInbox)) {
            assertThat(homeInbox.next()).isEqualTo("{\"type\":\"hello\",\"seq\":0}");
            assertThat(workInbox.next()).isEqualTo("{\"type\":\"hello\",\"seq\":0}");

            post("/api/messages", home.accessToken(), Map.of("messages", List.of(Map.of(
                    "id", UUID.randomUUID().toString(), "role", "user", "content", "你好", "createdAt", Instant.now().toString()))));

            // 两台设备都收到（包括发出消息的那台，它据此确认上传成功）
            assertThat(homeInbox.next()).isEqualTo("{\"type\":\"changed\",\"seq\":1}");
            assertThat(workInbox.next()).isEqualTo("{\"type\":\"changed\",\"seq\":1}");
        }
    }

    @Test
    void reconnectingDeviceLearnsTheLatestSequenceFromHello() throws Exception {
        var s = register();
        post("/api/messages", s.accessToken(), Map.of("messages", List.of(Map.of(
                "id", UUID.randomUUID().toString(), "role", "user", "content", "离线时别的设备发的", "createdAt", Instant.now().toString()))));
        var inbox = new Inbox();
        try (var ws = connect(s.accessToken(), inbox)) {
            // 断线期间错过的推送不重要：一连上就知道最新序号是 1，自己去拉
            assertThat(inbox.next()).isEqualTo("{\"type\":\"hello\",\"seq\":1}");
        }
    }

    @Test
    void otherUsersAreNotNotified() throws Exception {
        var alice = register();
        var bob = register();
        var bobInbox = new Inbox();
        try (var ws = connect(bob.accessToken(), bobInbox)) {
            bobInbox.next(); // hello
            post("/api/messages", alice.accessToken(), Map.of("messages", List.of(Map.of(
                    "id", UUID.randomUUID().toString(), "role", "user", "content", "x", "createdAt", Instant.now().toString()))));
            assertThat(bobInbox.messages.poll(1, TimeUnit.SECONDS)).isNull();
        }
    }

    @Test
    void heartbeat() throws Exception {
        var inbox = new Inbox();
        try (var ws = connect(register().accessToken(), inbox)) {
            inbox.next();
            ws.sendMessage(new TextMessage("{\"type\":\"ping\"}"));
            assertThat(inbox.next()).isEqualTo("{\"type\":\"pong\"}");
        }
    }

    @Test
    void handshakeNeedsAValidToken() {
        assertThatThrownBy(() -> connect(null, new Inbox())).hasMessageContaining("401");
        assertThatThrownBy(() -> connect("garbage", new Inbox())).hasMessageContaining("401");
    }
}
