package com.aila.server.auth;

import java.time.Instant;
import java.util.List;
import java.util.Optional;
import java.util.UUID;

import jakarta.persistence.LockModeType;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

public interface RefreshTokenRepository extends JpaRepository<RefreshToken, Long> {

    /** 加行锁：同一个令牌被并发刷新时，只有一个请求能换到新令牌 */
    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("select t from RefreshToken t join fetch t.user where t.tokenHash = :hash")
    Optional<RefreshToken> findForUpdate(@Param("hash") String hash);

    @Modifying
    @Query("update RefreshToken t set t.revokedAt = :now where t.familyId = :family and t.revokedAt is null")
    int revokeFamily(@Param("family") UUID family, @Param("now") Instant now);

    /** 每台设备当前有效的那一个令牌（没用过、没作废、没过期） */
    @Query("""
            select t from RefreshToken t
            where t.user.id = :userId and t.usedAt is null and t.revokedAt is null and t.expiresAt > :now
            order by t.createdAt desc
            """)
    List<RefreshToken> findActive(@Param("userId") long userId, @Param("now") Instant now);
}
