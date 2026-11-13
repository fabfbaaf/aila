package com.aila.server.auth;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.Map;

import org.junit.jupiter.api.Test;

import com.aila.server.IntegrationTest;

class AuthIntegrationTest extends IntegrationTest {

    @Test
    void registerThenSeeMyselfAndDevices() {
        var email = newEmail();
        var home = register(email, "home-pc");
        var work = login(email, "work-pc");

        var me = get("/api/me", work.accessToken());
        assertThat(me.getStatusCode().value()).isEqualTo(200);
        assertThat(me.getBody().get("user").get("email").asString()).isEqualTo(email);
        assertThat(me.getBody().get("user").get("displayName").asString()).isEqualTo(email.substring(0, email.indexOf('@')));
        assertThat(me.getBody().get("currentDeviceId").asString()).isEqualTo("work-pc");
        assertThat(me.getBody().get("devices").valueStream().map(d -> d.get("deviceId").asString()))
                .containsExactlyInAnyOrder("home-pc", "work-pc");
        assertThat(home.userId()).isEqualTo(work.userId());
    }

    @Test
    void emailIsUniqueAndCaseInsensitive() {
        var email = newEmail();
        register(email, "pc");
        var again = post("/api/auth/register", Map.of(
                "email", email.toUpperCase(), "password", "another-password", "deviceId", "pc2", "deviceName", "x"));
        assertThat(again.getStatusCode().value()).isEqualTo(409);
        assertThat(again.getBody().get("code").asString()).isEqualTo("EMAIL_TAKEN");

        // 登录时大小写、前后空格也不影响
        assertThat(login("  " + email.toUpperCase() + " ", "pc3").userId()).isPositive();
    }

    @Test
    void wrongPasswordAndUnknownEmailLookTheSame() {
        var email = newEmail();
        register(email, "pc");
        var wrong = post("/api/auth/login", Map.of("email", email, "password", "wrong-password", "deviceId", "pc", "deviceName", "x"));
        var unknown = post("/api/auth/login", Map.of("email", newEmail(), "password", "wrong-password", "deviceId", "pc", "deviceName", "x"));

        assertThat(wrong.getStatusCode().value()).isEqualTo(401);
        assertThat(unknown.getStatusCode().value()).isEqualTo(401);
        assertThat(wrong.getBody()).isEqualTo(unknown.getBody()); // 不暴露邮箱是否注册过
    }

    @Test
    void tooManyFailuresLockTheAccountForAWhile() {
        var email = newEmail();
        register(email, "pc");
        for (int i = 0; i < 5; i++) {
            post("/api/auth/login", Map.of("email", email, "password", "wrong-password", "deviceId", "pc", "deviceName", "x"));
        }
        var locked = post("/api/auth/login", Map.of("email", email, "password", "correct-horse", "deviceId", "pc", "deviceName", "x"));
        assertThat(locked.getStatusCode().value()).isEqualTo(429);
        assertThat(locked.getBody().get("code").asString()).isEqualTo("TOO_MANY_ATTEMPTS");
    }

    @Test
    void refreshTokenRotatesAndReuseKillsTheWholeLogin() {
        var s = register();
        var first = post("/api/auth/refresh", Map.of("refreshToken", s.refreshToken()));
        assertThat(first.getStatusCode().value()).isEqualTo(200);
        var newRefresh = first.getBody().get("refreshToken").asString();
        assertThat(newRefresh).isNotEqualTo(s.refreshToken());
        assertThat(get("/api/me", first.getBody().get("accessToken").asString()).getStatusCode().value()).isEqualTo(200);

        // 旧令牌又被用了一次（比如被偷了）：拒绝，并且连新令牌一起作废
        var replay = post("/api/auth/refresh", Map.of("refreshToken", s.refreshToken()));
        assertThat(replay.getStatusCode().value()).isEqualTo(401);
        assertThat(replay.getBody().get("code").asString()).isEqualTo("REFRESH_REUSED");
        assertThat(post("/api/auth/refresh", Map.of("refreshToken", newRefresh)).getStatusCode().value()).isEqualTo(401);
    }

    @Test
    void reuseOnOneDeviceDoesNotLogOutTheOthers() {
        var email = newEmail();
        var home = register(email, "home");
        var work = login(email, "work");
        post("/api/auth/refresh", Map.of("refreshToken", home.refreshToken()));
        post("/api/auth/refresh", Map.of("refreshToken", home.refreshToken())); // home 那串作废

        assertThat(post("/api/auth/refresh", Map.of("refreshToken", work.refreshToken())).getStatusCode().value()).isEqualTo(200);
    }

    @Test
    void logoutRevokesTheRefreshToken() {
        var s = register();
        assertThat(post("/api/auth/logout", Map.of("refreshToken", s.refreshToken())).getStatusCode().value()).isEqualTo(204);
        assertThat(post("/api/auth/refresh", Map.of("refreshToken", s.refreshToken())).getStatusCode().value()).isEqualTo(401);
        // 退出两次也没关系
        assertThat(post("/api/auth/logout", Map.of("refreshToken", s.refreshToken())).getStatusCode().value()).isEqualTo(204);
    }

    @Test
    void protectedApisNeedAValidAccessToken() {
        assertThat(http().get().uri("/api/me").retrieve().toBodilessEntity().getStatusCode().value()).isEqualTo(401);
        assertThat(get("/api/me", "not-a-jwt").getStatusCode().value()).isEqualTo(401);
        assertThat(get("/api/me", register().refreshToken()).getStatusCode().value()).isEqualTo(401);
    }

    @Test
    void validation() {
        var shortPassword = post("/api/auth/register", Map.of("email", newEmail(), "password", "short", "deviceId", "pc", "deviceName", "x"));
        assertThat(shortPassword.getStatusCode().value()).isEqualTo(400);
        assertThat(shortPassword.getBody().get("code").asString()).isEqualTo("VALIDATION");

        var badEmail = post("/api/auth/register", Map.of("email", "not-an-email", "password", "long-enough", "deviceId", "pc", "deviceName", "x"));
        assertThat(badEmail.getStatusCode().value()).isEqualTo(400);

        // 72 个字节以内才行：25 个汉字 = 75 字节
        var tooLong = post("/api/auth/register", Map.of("email", newEmail(), "password", "密".repeat(25), "deviceId", "pc", "deviceName", "x"));
        assertThat(tooLong.getStatusCode().value()).isEqualTo(400);
    }
}
