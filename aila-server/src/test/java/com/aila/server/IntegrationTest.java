package com.aila.server;

import java.util.Map;
import java.util.UUID;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.context.annotation.Import;
import org.springframework.http.HttpStatusCode;
import org.springframework.http.ResponseEntity;
import org.springframework.web.client.RestClient;

import tools.jackson.databind.JsonNode;

/**
 * 集成测试基类：在随机端口启动整个服务（内嵌 PostgreSQL），用真实的 HTTP 请求测。
 * 所有测试共用一个数据库，每个测试注册自己的用户，互不干扰。
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
@Import(EmbeddedPostgresConfig.class)
public abstract class IntegrationTest {

    @Value("${local.server.port}")
    protected int port;

    protected record Session(String accessToken, String refreshToken, long userId, String email) {
    }

    protected RestClient http() {
        return RestClient.builder()
                .baseUrl("http://localhost:" + port)
                .defaultStatusHandler(HttpStatusCode::isError, (req, res) -> {
                    // 不抛异常，让测试自己检查状态码
                })
                .build();
    }

    protected static String newEmail() {
        return "u" + UUID.randomUUID().toString().substring(0, 8) + "@test.aila";
    }

    protected ResponseEntity<JsonNode> post(String path, Object body) {
        return http().post().uri(path).body(body).retrieve().toEntity(JsonNode.class);
    }

    protected ResponseEntity<JsonNode> get(String path, String token) {
        return http().get().uri(path).header("Authorization", "Bearer " + token).retrieve().toEntity(JsonNode.class);
    }

    protected ResponseEntity<JsonNode> post(String path, String token, Object body) {
        return http().post().uri(path).header("Authorization", "Bearer " + token).body(body).retrieve()
                .toEntity(JsonNode.class);
    }

    protected Session register(String email, String deviceId) {
        var res = post("/api/auth/register", Map.of(
                "email", email, "password", "correct-horse", "deviceId", deviceId, "deviceName", "测试电脑 " + deviceId));
        if (res.getStatusCode().value() != 201) {
            throw new AssertionError("注册失败：" + res);
        }
        return session(res.getBody(), email);
    }

    protected Session register() {
        return register(newEmail(), "pc-1");
    }

    protected Session login(String email, String deviceId) {
        var res = post("/api/auth/login", Map.of(
                "email", email, "password", "correct-horse", "deviceId", deviceId, "deviceName", "测试电脑 " + deviceId));
        if (res.getStatusCode().value() != 200) {
            throw new AssertionError("登录失败：" + res);
        }
        return session(res.getBody(), email);
    }

    private static Session session(JsonNode body, String email) {
        return new Session(body.get("accessToken").asString(), body.get("refreshToken").asString(),
                body.get("user").get("id").asLong(), email);
    }
}
