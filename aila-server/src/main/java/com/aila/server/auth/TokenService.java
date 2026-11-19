package com.aila.server.auth;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.security.SecureRandom;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.util.Base64;
import java.util.HexFormat;
import java.util.UUID;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.security.oauth2.jose.jws.MacAlgorithm;
import org.springframework.security.oauth2.jwt.JwsHeader;
import org.springframework.security.oauth2.jwt.JwtClaimsSet;
import org.springframework.security.oauth2.jwt.JwtEncoder;
import org.springframework.security.oauth2.jwt.JwtEncoderParameters;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import com.aila.server.common.ApiException;
import com.aila.server.config.AilaProperties;
import com.aila.server.config.JwtConfig;

/**
 * 两种令牌：
 * - 访问令牌：JWT，15 分钟，每个请求都带，服务端只验签不查库
 * - 刷新令牌：随机串，30 天，只用来换新的访问令牌；存在数据库里，可以随时作废
 *
 * 刷新令牌每用一次就换一个新的（轮换）。旧令牌如果又被拿来用，说明它被偷了（或者客户端出了 bug），
 * 这时把同一次登录的整串令牌全部作废，小偷和用户都得重新登录。
 */
@Service
public class TokenService {

    private static final Logger log = LoggerFactory.getLogger(TokenService.class);
    public static final String DEVICE_CLAIM = "did";
    private static final SecureRandom RANDOM = new SecureRandom();

    public record Session(String accessToken, long expiresIn, String refreshToken, User user) {
    }

    private final JwtEncoder encoder;
    private final RefreshTokenRepository tokens;
    private final Duration accessTtl;
    private final Duration refreshTtl;
    private final Clock clock;

    public TokenService(JwtEncoder encoder, RefreshTokenRepository tokens, AilaProperties props, Clock clock) {
        this.encoder = encoder;
        this.tokens = tokens;
        this.accessTtl = props.jwt().accessTtl();
        this.refreshTtl = props.jwt().refreshTtl();
        this.clock = clock;
    }

    /** 登录或注册成功：发一对新令牌，开始一个新的 family */
    @Transactional
    public Session issue(User user, String deviceId, String deviceName) {
        return issue(user, deviceId, deviceName, UUID.randomUUID());
    }

    /** 用刷新令牌换一对新令牌 */
    @Transactional(noRollbackFor = ApiException.class) // 发现盗用时要把作废写进数据库，不能跟着异常回滚
    public Session rotate(String rawRefreshToken) {
        var now = clock.instant();
        var token = tokens.findForUpdate(hash(rawRefreshToken)).orElseThrow(TokenService::invalid);
        if (token.getRevokedAt() != null) {
            throw invalid();
        }
        if (token.getUsedAt() != null) {
            tokens.revokeFamily(token.getFamilyId(), now);
            log.warn("刷新令牌被重复使用，作废这次登录（用户 {}，设备 {}）", token.getUser().getId(), token.getDeviceName());
            throw new ApiException(HttpStatus.UNAUTHORIZED, "REFRESH_REUSED", "登录状态异常，请重新登录");
        }
        if (!token.getExpiresAt().isAfter(now)) {
            throw new ApiException(HttpStatus.UNAUTHORIZED, "REFRESH_EXPIRED", "登录已过期，请重新登录");
        }
        token.markUsed(now);
        return issue(token.getUser(), token.getDeviceId(), token.getDeviceName(), token.getFamilyId());
    }

    /** 退出登录：这台设备的整串令牌作废。令牌无效时什么都不做（退出登录总是成功） */
    @Transactional
    public void revoke(String rawRefreshToken) {
        tokens.findForUpdate(hash(rawRefreshToken)).ifPresent(t -> tokens.revokeFamily(t.getFamilyId(), clock.instant()));
    }

    private Session issue(User user, String deviceId, String deviceName, UUID family) {
        var now = clock.instant();
        var claims = JwtClaimsSet.builder()
                .issuer(JwtConfig.ISSUER)
                .subject(user.getId().toString())
                .issuedAt(now)
                .expiresAt(now.plus(accessTtl))
                .claim(DEVICE_CLAIM, deviceId)
                .build();
        var access = encoder.encode(JwtEncoderParameters.from(JwsHeader.with(MacAlgorithm.HS256).build(), claims));

        var raw = randomToken();
        tokens.save(new RefreshToken(user, hash(raw), family, deviceId, deviceName, now, now.plus(refreshTtl)));
        return new Session(access.getTokenValue(), accessTtl.toSeconds(), raw, user);
    }

    private static ApiException invalid() {
        return new ApiException(HttpStatus.UNAUTHORIZED, "REFRESH_INVALID", "登录已失效，请重新登录");
    }

    private static String randomToken() {
        var bytes = new byte[32];
        RANDOM.nextBytes(bytes);
        return Base64.getUrlEncoder().withoutPadding().encodeToString(bytes);
    }

    static String hash(String raw) {
        try {
            var digest = MessageDigest.getInstance("SHA-256").digest(raw.getBytes(StandardCharsets.UTF_8));
            return HexFormat.of().formatHex(digest);
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
    }
}
