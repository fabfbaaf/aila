package com.aila.server.auth;

import java.time.Instant;
import java.util.List;

import jakarta.validation.constraints.Email;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

/** 账号相关接口的请求和响应 */
public final class AuthDtos {

    private AuthDtos() {
    }

    /** deviceId 由桌面端第一次启动时生成并保存，用来区分「家里的电脑」和「公司的电脑」 */
    public record RegisterRequest(
            @NotBlank @Email @Size(max = 254) String email,
            @NotBlank @Size(min = 8, max = 72) String password,
            @Size(max = 50) String displayName,
            @NotBlank @Size(max = 64) String deviceId,
            @NotBlank @Size(max = 100) String deviceName) {
    }

    public record LoginRequest(
            @NotBlank @Size(max = 254) String email,
            @NotBlank @Size(max = 72) String password,
            @NotBlank @Size(max = 64) String deviceId,
            @NotBlank @Size(max = 100) String deviceName) {
    }

    public record RefreshRequest(@NotBlank @Size(max = 100) String refreshToken) {
    }

    public record UserDto(long id, String email, String displayName) {

        static UserDto of(User u) {
            return new UserDto(u.getId(), u.getEmail(), u.getDisplayName());
        }
    }

    /** expiresIn：访问令牌还有多少秒过期，客户端提前一点刷新 */
    public record TokenResponse(String accessToken, String tokenType, long expiresIn, String refreshToken, UserDto user) {

        static TokenResponse of(TokenService.Session s) {
            return new TokenResponse(s.accessToken(), "Bearer", s.expiresIn(), s.refreshToken(), UserDto.of(s.user()));
        }
    }

    /** lastActiveAt：这台设备最近一次登录或刷新令牌的时间 */
    public record DeviceDto(String deviceId, String deviceName, Instant lastActiveAt) {
    }

    public record MeResponse(UserDto user, String currentDeviceId, List<DeviceDto> devices) {
    }
}
