package com.aila.server.auth;

import java.nio.charset.StandardCharsets;
import java.util.Locale;

import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.http.HttpStatus;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import com.aila.server.auth.AuthDtos.LoginRequest;
import com.aila.server.auth.AuthDtos.RegisterRequest;
import com.aila.server.common.ApiException;

@Service
public class AuthService {

    /** BCrypt 只看密码的前 72 个字节，超出的部分会被忽略（中文一个字 3 个字节） */
    private static final int BCRYPT_MAX_BYTES = 72;

    private final UserRepository users;
    private final PasswordEncoder encoder;
    private final TokenService tokens;
    private final LoginThrottle throttle;
    /** 邮箱不存在时也算一次 BCrypt，让响应时间和「密码错误」差不多，别人没法靠时间差试出哪些邮箱注册过 */
    private final String dummyHash;

    public AuthService(UserRepository users, PasswordEncoder encoder, TokenService tokens, LoginThrottle throttle) {
        this.users = users;
        this.encoder = encoder;
        this.tokens = tokens;
        this.throttle = throttle;
        this.dummyHash = encoder.encode("aila-dummy-password");
    }

    @Transactional
    public TokenService.Session register(RegisterRequest r) {
        var email = normalize(r.email());
        if (r.password().getBytes(StandardCharsets.UTF_8).length > BCRYPT_MAX_BYTES) {
            throw new ApiException(HttpStatus.BAD_REQUEST, "VALIDATION", "密码太长了（最多 72 个字节）");
        }
        if (users.existsByEmail(email)) {
            throw emailTaken();
        }
        var name = r.displayName() == null || r.displayName().isBlank()
                ? email.substring(0, email.indexOf('@'))
                : r.displayName().trim();
        User user;
        try {
            user = users.saveAndFlush(new User(email, encoder.encode(r.password()), name));
        } catch (DataIntegrityViolationException e) {
            throw emailTaken(); // 两个请求同时注册同一个邮箱，唯一索引兜底
        }
        return tokens.issue(user, r.deviceId(), r.deviceName());
    }

    @Transactional
    public TokenService.Session login(LoginRequest r) {
        var email = normalize(r.email());
        throttle.check(email);
        var user = users.findByEmail(email).orElse(null);
        boolean ok = encoder.matches(r.password(), user == null ? dummyHash : user.getPasswordHash());
        if (!ok || user == null) {
            throttle.failed(email);
            throw new ApiException(HttpStatus.UNAUTHORIZED, "BAD_CREDENTIALS", "邮箱或密码不对");
        }
        throttle.succeeded(email);
        return tokens.issue(user, r.deviceId(), r.deviceName());
    }

    static String normalize(String email) {
        return email.trim().toLowerCase(Locale.ROOT);
    }

    private static ApiException emailTaken() {
        return new ApiException(HttpStatus.CONFLICT, "EMAIL_TAKEN", "这个邮箱已经注册过了");
    }
}
