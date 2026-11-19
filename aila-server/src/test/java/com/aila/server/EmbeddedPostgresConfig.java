package com.aila.server;

import java.io.IOException;

import javax.sql.DataSource;

import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.context.annotation.Bean;

import com.zaxxer.hikari.HikariDataSource;

import io.zonky.test.db.postgres.embedded.EmbeddedPostgres;

/**
 * 内嵌的真实 PostgreSQL（16 版，和正式环境一致），不需要 Docker，也不用自己装数据库。
 * 第一次运行会从 Maven 下载 PostgreSQL 程序，之后很快。
 */
@TestConfiguration(proxyBeanMethods = false)
public class EmbeddedPostgresConfig {

    @Bean(destroyMethod = "close")
    EmbeddedPostgres embeddedPostgres() throws IOException {
        return EmbeddedPostgres.builder().start();
    }

    @Bean
    DataSource dataSource(EmbeddedPostgres postgres) {
        var ds = new HikariDataSource();
        // 用 127.0.0.1 不用 localhost：localhost 会先试 IPv6 的 ::1，有的电脑上（开着代理的虚拟网卡）IPv6 回环被拦，
        // 每次连接都要等它失败再退回 IPv4，测试变得又慢又时好时坏
        ds.setJdbcUrl(postgres.getJdbcUrl("postgres", "postgres").replace("//localhost:", "//127.0.0.1:"));
        ds.setUsername("postgres");
        ds.setMaximumPoolSize(20);
        return ds;
    }
}
