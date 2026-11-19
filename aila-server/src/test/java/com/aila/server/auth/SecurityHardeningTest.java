package com.aila.server.auth;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.net.URI;
import java.time.Instant;
import java.util.Map;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.TimeUnit;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.security.oauth2.jose.jws.MacAlgorithm;
import org.springframework.security.oauth2.jwt.JwsHeader;
import org.springframework.security.oauth2.jwt.JwtClaimsSet;
import org.springframework.security.oauth2.jwt.JwtEncoder;
import org.springframework.security.oauth2.jwt.JwtEncoderParameters;
import org.springframework.web.socket.CloseStatus;
import org.springframework.web.socket.TextMessage;
import org.springframework.web.socket.WebSocketHttpHeaders;
import org.springframework.web.socket.WebSocketSession;
import org.springframework.web.socket.client.standard.StandardWebSocketClient;
import org.springframework.web.socket.handler.TextWebSocketHandler;

import com.aila.server.IntegrationTest;

/** 安全检查（2026-11）补上的几条 */
class SecurityHardeningTest extends IntegrationTest {

    @Autowired
    JwtEncoder encoder;

    @Autowired
    AuthService auth;

    private String token(String issuer, long userId, Instant expiresAt) {
        var claims = JwtClaimsSet.builder()
                .issuer(issuer)
                .subject(Long.toString(userId))
                .issuedAt(expiresAt.minusSeconds(900))
                .expiresAt(expiresAt)
                .claim(TokenService.DEVICE_CLAIM, "pc-1")
                .build();
        return encoder.encode(JwtEncoderParameters.from(JwsHeader.with(MacAlgorithm.HS256).build(), claims)).getTokenValue();
    }

    @Test
    void tokensFromAnotherIssuerAreRejected() {
        var s = register();
        var later = Instant.now().plusSeconds(600);
        assertThat(get("/api/me", token("aila", s.userId(), later)).getStatusCode().value()).isEqualTo(200);
        assertThat(get("/api/me", token("someone-else", s.userId(), later)).getStatusCode().value()).isEqualTo(401);
    }

    @Test
    void metricsAreNotExposed() {
        var s = register();
        assertThat(http().get().uri("/actuator/health").retrieve().toBodilessEntity().getStatusCode().value()).isEqualTo(200);
        assertThat(get("/actuator/metrics", s.accessToken()).getStatusCode().value()).isNotEqualTo(200);
    }

    @Test
    void wrongPasswordsFromOneAddressDoNotLockOutAnother() {
        var email = newEmail();
        register(email, "pc");
        for (int i = 0; i < 5; i++) {
            post("/api/auth/login", Map.of("email", email, "password", "wrong-password", "deviceId", "pc", "deviceName", "x"));
        }
        var here = post("/api/auth/login", Map.of("email", email, "password", "correct-horse", "deviceId", "pc", "deviceName", "x"));
        assertThat(here.getStatusCode().value()).isEqualTo(429);

        // 从另一个地址登录：不受影响。直接调登录服务、给它另一个 IP（以前用 IPv6 的本机 [::1] 连，
        // 有的电脑上 IPv6 回环连不上，测试就时好时坏）
        var other = auth.login(new AuthDtos.LoginRequest(email, "correct-horse", "pc", "x"), "203.0.113.9");
        assertThat(other.user().getEmail()).isEqualTo(email);
        // 同一个地址还锁着
        assertThatThrownBy(() -> auth.login(new AuthDtos.LoginRequest(email, "correct-horse", "pc", "x"), "127.0.0.1"))
                .hasMessageContaining("错误次数太多");
    }

    @Test
    void syncSocketIsClosedOnceTheAccessTokenExpires() throws Exception {
        var s = register();
        // 刚过期几秒：握手时还在允许的时钟误差（60 秒）里，能连上；之后第一次心跳就被断开
        var expired = token("aila", s.userId(), Instant.now().minusSeconds(5));
        var closed = new CompletableFuture<CloseStatus>();
        var headers = new WebSocketHttpHeaders();
        headers.setBearerAuth(expired);
        var session = new StandardWebSocketClient().execute(new TextWebSocketHandler() {
            @Override
            public void afterConnectionClosed(WebSocketSession session, CloseStatus status) {
                closed.complete(status);
            }
        }, headers, URI.create("ws://localhost:" + port + "/ws/sync")).get(5, TimeUnit.SECONDS);
        session.sendMessage(new TextMessage("{\"type\":\"ping\"}"));
        assertThat(closed.get(5, TimeUnit.SECONDS).getCode()).isEqualTo(4401);
    }
}
