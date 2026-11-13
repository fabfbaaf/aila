package com.aila.server.config;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.socket.config.annotation.EnableWebSocket;
import org.springframework.web.socket.config.annotation.WebSocketConfigurer;
import org.springframework.web.socket.config.annotation.WebSocketHandlerRegistry;
import org.springframework.web.socket.server.standard.ServletServerContainerFactoryBean;

import com.aila.server.sync.SyncSocketHandler;

@Configuration(proxyBeanMethods = false)
@EnableWebSocket
public class WebSocketConfig implements WebSocketConfigurer {

    private final SyncSocketHandler syncHandler;

    public WebSocketConfig(SyncSocketHandler syncHandler) {
        this.syncHandler = syncHandler;
    }

    @Override
    public void registerWebSocketHandlers(WebSocketHandlerRegistry registry) {
        // 不放开跨域：浏览器里的网页带不上 Authorization 头，桌面端（Node）不受同源限制
        registry.addHandler(syncHandler, "/ws/sync");
    }

    /** 客户端每 25 秒发一次心跳，70 秒没有任何消息就认为对方已经不在了 */
    @Bean
    ServletServerContainerFactoryBean webSocketContainer() {
        var container = new ServletServerContainerFactoryBean();
        container.setMaxSessionIdleTimeout(70_000L);
        container.setMaxTextMessageBufferSize(8 * 1024);
        return container;
    }
}
