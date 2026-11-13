package com.aila.server.auth;

import java.time.Instant;
import java.util.UUID;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.FetchType;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.JoinColumn;
import jakarta.persistence.ManyToOne;
import jakarta.persistence.Table;

/** 刷新令牌。只存 SHA-256 哈希，原文只在发给客户端时出现一次 */
@Entity
@Table(name = "refresh_tokens")
public class RefreshToken {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "user_id")
    private User user;

    @Column(name = "token_hash", nullable = false, unique = true)
    private String tokenHash;

    /** 同一次登录不断刷新换出来的令牌共用一个 family */
    @Column(name = "family_id", nullable = false)
    private UUID familyId;

    @Column(name = "device_id", nullable = false)
    private String deviceId;

    @Column(name = "device_name", nullable = false)
    private String deviceName;

    @Column(name = "created_at", nullable = false)
    private Instant createdAt;

    @Column(name = "expires_at", nullable = false)
    private Instant expiresAt;

    /** 已经换过新令牌 */
    @Column(name = "used_at")
    private Instant usedAt;

    /** 退出登录或发现被盗用 */
    @Column(name = "revoked_at")
    private Instant revokedAt;

    protected RefreshToken() {
    }

    public RefreshToken(User user, String tokenHash, UUID familyId, String deviceId, String deviceName,
            Instant now, Instant expiresAt) {
        this.user = user;
        this.tokenHash = tokenHash;
        this.familyId = familyId;
        this.deviceId = deviceId;
        this.deviceName = deviceName;
        this.createdAt = now;
        this.expiresAt = expiresAt;
    }

    public void markUsed(Instant now) {
        this.usedAt = now;
    }

    public User getUser() {
        return user;
    }

    public UUID getFamilyId() {
        return familyId;
    }

    public String getDeviceId() {
        return deviceId;
    }

    public String getDeviceName() {
        return deviceName;
    }

    public Instant getCreatedAt() {
        return createdAt;
    }

    public Instant getExpiresAt() {
        return expiresAt;
    }

    public Instant getUsedAt() {
        return usedAt;
    }

    public Instant getRevokedAt() {
        return revokedAt;
    }
}
