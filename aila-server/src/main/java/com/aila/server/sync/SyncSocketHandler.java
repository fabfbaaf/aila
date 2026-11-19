package com.aila.server.sync;

import java.io.IOException;
import java.time.Clock;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.security.oauth2.server.resource.authentication.JwtAuthenticationToken;
import org.springframework.stereotype.Component;
import org.springframework.web.socket.CloseStatus;
import org.springframework.web.socket.TextMessage;
import org.springframework.web.socket.WebSocketSession;
import org.springframework.web.socket.handler.ConcurrentWebSocketSessionDecorator;
import org.springframework.web.socket.handler.TextWebSocketHandler;

import io.micrometer.core.instrument.Gauge;
import io.micrometer.core.instrument.MeterRegistry;

/**
 * /ws/sync：只推「有新变更了，最新序号是 N」，不推数据本身。客户端收到后用 /api/sync?since= 去拉。
 *
 * 这样推送丢了也没关系：客户端重连时会收到 hello（带最新序号），对比自己的序号就知道要不要拉，
 * 所以断线、重连、服务端重启都不会丢数据。推送只是让同步更及时。
 *
 * 握手请求要带 Authorization: Bearer 访问令牌，在 Spring Security 那一层就校验了。
 * 握手之后连接会一直开着：令牌过期以后（退出登录、刷新令牌被作废都会让它续不上），
 * 下一次心跳或推送时断开，客户端拿新令牌重连，拿不到就不再收推送。
 */
@Component
public class SyncSocketHandler extends TextWebSocketHandler {

    private static final Logger log = LoggerFactory.getLogger(SyncSocketHandler.class);
    /** 发送超过 5 秒或积压超过 64KB 的连接直接断开，免得一个卡住的客户端拖住推送线程 */
    private static final int SEND_TIME_LIMIT_MS = 5000;
    private static final int BUFFER_LIMIT_BYTES = 64 * 1024;

    /** 用户 → 这个用户的所有连接（每台设备一个） */
    private final Map<Long, Map<String, WebSocketSession>> sessions = new ConcurrentHashMap<>();
    /** 令牌过期断开用的关闭码（4000 起是应用自己定义的） */
    static final CloseStatus TOKEN_EXPIRED = new CloseStatus(4401, "访问令牌过期了");

    private final JdbcClient jdbc;
    private final Clock clock;

    public SyncSocketHandler(JdbcClient jdbc, MeterRegistry meters, Clock clock) {
        this.jdbc = jdbc;
        this.clock = clock;
        Gauge.builder("aila.sync.connections", this, SyncSocketHandler::connectionCount)
                .description("当前在线的同步连接数")
                .register(meters);
    }

    @Override
    public void afterConnectionEstablished(WebSocketSession raw) throws IOException {
        long userId = userId(raw);
        // 原始 session 不能被多个线程同时发送，包一层：多个推送同时到达时排队发送
        var session = new ConcurrentWebSocketSessionDecorator(raw, SEND_TIME_LIMIT_MS, BUFFER_LIMIT_BYTES);
        sessions.computeIfAbsent(userId, k -> new ConcurrentHashMap<>()).put(raw.getId(), session);
        long seq = jdbc.sql("select last_seq from users where id = :id").param("id", userId).query(Long.class).single();
        session.sendMessage(new TextMessage("{\"type\":\"hello\",\"seq\":" + seq + "}"));
    }

    @Override
    protected void handleTextMessage(WebSocketSession raw, TextMessage message) throws IOException {
        // 应用层心跳：客户端每隔一会儿发 ping，收不到 pong 就认为连接已经断了，主动重连
        if (expired(raw)) {
            closeQuietly(raw, TOKEN_EXPIRED);
            return;
        }
        if (message.getPayload().contains("\"ping\"")) {
            var session = find(raw);
            if (session != null) {
                session.sendMessage(new TextMessage("{\"type\":\"pong\"}"));
            }
        }
    }

    @Override
    public void handleTransportError(WebSocketSession raw, Throwable exception) throws IOException {
        raw.close(CloseStatus.SERVER_ERROR);
    }

    @Override
    public void afterConnectionClosed(WebSocketSession raw, CloseStatus status) {
        var userSessions = sessions.get(userId(raw));
        if (userSessions != null) {
            userSessions.remove(raw.getId());
        }
    }

    /** 事务提交后调用：告诉这个用户的所有设备「最新序号是 seq」 */
    public void notifyChanged(long userId, long seq) {
        var userSessions = sessions.get(userId);
        if (userSessions == null) {
            return;
        }
        var message = new TextMessage("{\"type\":\"changed\",\"seq\":" + seq + "}");
        for (var session : userSessions.values()) {
            if (expired(session)) {
                closeQuietly(session, TOKEN_EXPIRED);
                continue;
            }
            try {
                session.sendMessage(message);
            } catch (IOException | RuntimeException e) {
                // 发不出去的连接关掉，客户端会重连并补拉
                log.debug("推送失败，关闭连接 {}", session.getId(), e);
                closeQuietly(session, CloseStatus.SESSION_NOT_RELIABLE);
            }
        }
    }

    int connectionCount() {
        return sessions.values().stream().mapToInt(Map::size).sum();
    }

    private WebSocketSession find(WebSocketSession raw) {
        var userSessions = sessions.get(userId(raw));
        return userSessions == null ? null : userSessions.get(raw.getId());
    }

    private boolean expired(WebSocketSession session) {
        if (session.getPrincipal() instanceof JwtAuthenticationToken token) {
            var exp = token.getToken().getExpiresAt();
            return exp != null && !clock.instant().isBefore(exp);
        }
        return true;
    }

    private static long userId(WebSocketSession session) {
        if (session.getPrincipal() instanceof JwtAuthenticationToken token) {
            return Long.parseLong(token.getToken().getSubject());
        }
        throw new IllegalStateException("同步连接没有登录信息");
    }

    private static void closeQuietly(WebSocketSession session, CloseStatus status) {
        try {
            session.close(status);
        } catch (IOException ignored) {
            // 已经断了
        }
    }
}
