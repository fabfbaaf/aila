package com.aila.server.config;

import java.security.SecureRandom;
import java.util.Base64;

import javax.crypto.SecretKey;
import javax.crypto.spec.SecretKeySpec;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.security.oauth2.jose.jws.MacAlgorithm;
import org.springframework.security.oauth2.jwt.JwtDecoder;
import org.springframework.security.oauth2.jwt.JwtEncoder;
import org.springframework.security.oauth2.jwt.NimbusJwtDecoder;
import org.springframework.security.oauth2.jwt.NimbusJwtEncoder;

import com.nimbusds.jose.jwk.source.ImmutableSecret;

/**
 * 访问令牌用 HS256 签名的 JWT：服务端只有一个实例，对称密钥最简单。
 * 以后拆成多个服务、需要别的服务也能验证时，换成 RS256 公私钥。
 */
@Configuration(proxyBeanMethods = false)
public class JwtConfig {

    private static final Logger log = LoggerFactory.getLogger(JwtConfig.class);
    private static final int MIN_KEY_BYTES = 32;

    @Bean
    SecretKey jwtKey(AilaProperties props) {
        var secret = props.jwt().secret();
        byte[] bytes;
        if (secret == null || secret.isBlank()) {
            bytes = new byte[MIN_KEY_BYTES];
            new SecureRandom().nextBytes(bytes);
            log.warn("没有配置 AILA_JWT_SECRET，本次启动使用随机密钥（重启后访问令牌全部失效，正式部署必须配置）");
        } else {
            bytes = Base64.getDecoder().decode(secret.trim());
            if (bytes.length < MIN_KEY_BYTES) {
                throw new IllegalStateException("AILA_JWT_SECRET 至少要 32 字节（Base64 编码前）");
            }
        }
        return new SecretKeySpec(bytes, "HmacSHA256");
    }

    @Bean
    JwtEncoder jwtEncoder(SecretKey jwtKey) {
        return new NimbusJwtEncoder(new ImmutableSecret<>(jwtKey));
    }

    @Bean
    JwtDecoder jwtDecoder(SecretKey jwtKey) {
        return NimbusJwtDecoder.withSecretKey(jwtKey).macAlgorithm(MacAlgorithm.HS256).build();
    }
}
