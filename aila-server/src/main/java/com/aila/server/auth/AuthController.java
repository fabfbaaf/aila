package com.aila.server.auth;

import java.time.Clock;
import java.util.LinkedHashMap;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;

import org.springframework.http.HttpStatus;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

import com.aila.server.auth.AuthDtos.DeviceDto;
import com.aila.server.auth.AuthDtos.LoginRequest;
import com.aila.server.auth.AuthDtos.MeResponse;
import com.aila.server.auth.AuthDtos.RefreshRequest;
import com.aila.server.auth.AuthDtos.RegisterRequest;
import com.aila.server.auth.AuthDtos.TokenResponse;
import com.aila.server.auth.AuthDtos.UserDto;
import com.aila.server.common.ApiException;

@RestController
public class AuthController {

    private final AuthService auth;
    private final TokenService tokens;
    private final UserRepository users;
    private final RefreshTokenRepository refreshTokens;
    private final Clock clock;
    private final AuthRateLimit rateLimit;

    public AuthController(AuthService auth, TokenService tokens, UserRepository users,
            RefreshTokenRepository refreshTokens, Clock clock, AuthRateLimit rateLimit) {
        this.rateLimit = rateLimit;
        this.auth = auth;
        this.tokens = tokens;
        this.users = users;
        this.refreshTokens = refreshTokens;
        this.clock = clock;
    }

    @PostMapping("/api/auth/register")
    @ResponseStatus(HttpStatus.CREATED)
    TokenResponse register(@Valid @RequestBody RegisterRequest r, HttpServletRequest req) {
        rateLimit.check(req.getRemoteAddr());
        return TokenResponse.of(auth.register(r));
    }

    @PostMapping("/api/auth/login")
    TokenResponse login(@Valid @RequestBody LoginRequest r, HttpServletRequest req) {
        rateLimit.check(req.getRemoteAddr());
        return TokenResponse.of(auth.login(r, req.getRemoteAddr()));
    }

    @PostMapping("/api/auth/refresh")
    TokenResponse refresh(@Valid @RequestBody RefreshRequest r) {
        return TokenResponse.of(tokens.rotate(r.refreshToken()));
    }

    @PostMapping("/api/auth/logout")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    void logout(@Valid @RequestBody RefreshRequest r) {
        tokens.revoke(r.refreshToken());
    }

    /** 当前账号，以及登录着的设备 */
    @GetMapping("/api/me")
    MeResponse me(@AuthenticationPrincipal Jwt jwt) {
        long userId = Long.parseLong(jwt.getSubject());
        var user = users.findById(userId)
                .orElseThrow(() -> new ApiException(HttpStatus.UNAUTHORIZED, "USER_GONE", "账号不存在"));
        var devices = new LinkedHashMap<String, DeviceDto>();
        for (var t : refreshTokens.findActive(userId, clock.instant())) {
            devices.putIfAbsent(t.getDeviceId(), new DeviceDto(t.getDeviceId(), t.getDeviceName(), t.getCreatedAt()));
        }
        return new MeResponse(UserDto.of(user), jwt.getClaimAsString(TokenService.DEVICE_CLAIM),
                devices.values().stream().toList());
    }
}
