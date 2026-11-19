package com.aila.server.config;

import java.time.Duration;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.context.properties.bind.DefaultValue;

@ConfigurationProperties("aila")
public record AilaProperties(@DefaultValue Jwt jwt, @DefaultValue Login login, @DefaultValue AuthLimit authLimit) {

    public record Jwt(
            @DefaultValue("") String secret,
            @DefaultValue("15m") Duration accessTtl,
            @DefaultValue("30d") Duration refreshTtl) {
    }

    public record Login(@DefaultValue("5") int maxFailures, @DefaultValue("15m") Duration lockDuration) {
    }

    public record AuthLimit(@DefaultValue("30") int requests, @DefaultValue("10m") Duration window) {
    }
}
