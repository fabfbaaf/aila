package com.aila.server.auth;

import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.util.concurrent.ConcurrentHashMap;

import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Component;

import com.aila.server.common.ApiException;
import com.aila.server.config.AilaProperties;

/**
 * 按 IP 限制注册、登录的总次数：防撞库（拿一堆邮箱密码来试）和批量注册。
 * 固定时间窗：窗口内超过上限就返回 429，窗口过了重新计数。正常人一台电脑登录几次远到不了上限。
 * 和 {@link LoginThrottle} 一样存在内存里，只适合单实例。
 */
@Component
public class AuthRateLimit {

    private record Window(Instant start, int count) {
    }

    private static final int MAX_TRACKED = 10_000;

    private final ConcurrentHashMap<String, Window> windows = new ConcurrentHashMap<>();
    private final int limit;
    private final Duration length;
    private final Clock clock;

    public AuthRateLimit(AilaProperties props, Clock clock) {
        this.limit = props.authLimit().requests();
        this.length = props.authLimit().window();
        this.clock = clock;
    }

    public void check(String ip) {
        var now = clock.instant();
        if (windows.size() > MAX_TRACKED) {
            windows.values().removeIf(w -> !now.isBefore(w.start().plus(length)));
        }
        var w = windows.compute(ip, (k, old) -> old == null || !now.isBefore(old.start().plus(length))
                ? new Window(now, 1)
                : new Window(old.start(), old.count() + 1));
        if (w.count() > limit) {
            var minutes = Math.max(1, Duration.between(now, w.start().plus(length)).toMinutes());
            throw new ApiException(HttpStatus.TOO_MANY_REQUESTS, "TOO_MANY_REQUESTS",
                    "这台电脑登录、注册的次数太多了，" + minutes + " 分钟后再试");
        }
    }
}
