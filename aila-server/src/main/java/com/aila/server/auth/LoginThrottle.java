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
 * 防暴力破解：同一个 IP 对同一个邮箱在一段时间内连续输错太多次，就锁定一会儿。
 * 键是「邮箱|IP」：只按邮箱锁的话，谁都能故意输错几次把别人的账号锁住。
 * 换着 IP 来试的，由 {@link AuthRateLimit} 按 IP 限总次数，加上 BCrypt 本身慢。
 * 存在内存里，只适合单实例；以后部署多个实例时换成 Redis。
 */
@Component
public class LoginThrottle {

    private record Attempts(int failures, Instant firstFailure, Instant lockedUntil) {
    }

    private static final int MAX_TRACKED = 10_000;

    private final ConcurrentHashMap<String, Attempts> attempts = new ConcurrentHashMap<>();
    private final int maxFailures;
    private final Duration window;
    private final Clock clock;

    public LoginThrottle(AilaProperties props, Clock clock) {
        this.maxFailures = props.login().maxFailures();
        this.window = props.login().lockDuration();
        this.clock = clock;
    }

    public void check(String email) {
        var a = attempts.get(email);
        if (a != null && a.lockedUntil() != null && clock.instant().isBefore(a.lockedUntil())) {
            var minutes = Math.max(1, Duration.between(clock.instant(), a.lockedUntil()).toMinutes());
            throw new ApiException(HttpStatus.TOO_MANY_REQUESTS, "TOO_MANY_ATTEMPTS",
                    "密码错误次数太多，请 " + minutes + " 分钟后再试");
        }
    }

    public void failed(String email) {
        var now = clock.instant();
        if (attempts.size() > MAX_TRACKED) {
            // 有人拿大量不同邮箱来试：清掉已经过期的记录，防止内存一直涨
            attempts.values().removeIf(a -> a.firstFailure().plus(window).isBefore(now));
        }
        attempts.compute(email, (k, a) -> {
            boolean fresh = a == null
                    || a.firstFailure().plus(window).isBefore(now)
                    || (a.lockedUntil() != null && !now.isBefore(a.lockedUntil()));
            int failures = fresh ? 1 : a.failures() + 1;
            Instant first = fresh ? now : a.firstFailure();
            return new Attempts(failures, first, failures >= maxFailures ? now.plus(window) : null);
        });
    }

    public void succeeded(String email) {
        attempts.remove(email);
    }
}
