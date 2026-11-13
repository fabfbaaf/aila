package com.aila.server;

import org.springframework.boot.SpringApplication;

/**
 * 不装数据库也能跑：./mvnw spring-boot:test-run
 * 用内嵌的 PostgreSQL 启动整个服务，数据放在临时目录，关掉就没了。适合调试桌面端的同步。
 */
public class TestAilaServerApplication {

    public static void main(String[] args) {
        SpringApplication.from(AilaServerApplication::main).with(EmbeddedPostgresConfig.class).run(args);
    }
}
