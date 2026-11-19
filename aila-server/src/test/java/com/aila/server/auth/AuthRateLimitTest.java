package com.aila.server.auth;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.time.ZoneId;

import org.junit.jupiter.api.Test;

import com.aila.server.common.ApiException;
import com.aila.server.config.AilaProperties;

class AuthRateLimitTest {

    /** 能往前拨的时钟 */
    private static final class FakeClock extends Clock {
        Instant now = Instant.parse("2026-11-18T08:00:00Z");

        @Override
        public ZoneId getZone() {
            return ZoneId.of("UTC");
        }

        @Override
        public Clock withZone(ZoneId zone) {
            return this;
        }

        @Override
        public Instant instant() {
            return now;
        }
    }

    private static AilaProperties props(int requests, Duration window) {
        return new AilaProperties(
                new AilaProperties.Jwt("", Duration.ofMinutes(15), Duration.ofDays(30)),
                new AilaProperties.Login(5, Duration.ofMinutes(15)),
                new AilaProperties.AuthLimit(requests, window));
    }

    @Test
    void tooManyRequestsFromOneIpAreRejectedUntilTheWindowEnds() {
        var clock = new FakeClock();
        var limit = new AuthRateLimit(props(3, Duration.ofMinutes(10)), clock);
        for (int i = 0; i < 3; i++) {
            limit.check("10.0.0.1");
        }
        assertThatThrownBy(() -> limit.check("10.0.0.1"))
                .isInstanceOf(ApiException.class)
                .satisfies(e -> assertThat(((ApiException) e).code()).isEqualTo("TOO_MANY_REQUESTS"))
                .hasMessageContaining("10 分钟后");
        limit.check("10.0.0.2"); // 别的 IP 不受影响

        clock.now = clock.now.plus(Duration.ofMinutes(10));
        limit.check("10.0.0.1"); // 新的时间窗
    }
}
