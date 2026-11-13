package com.aila.server;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.properties.ConfigurationPropertiesScan;

/** 艾拉的云端后端：账号登录，对话和设置在多台设备之间同步 */
@SpringBootApplication
@ConfigurationPropertiesScan
public class AilaServerApplication {

    public static void main(String[] args) {
        SpringApplication.run(AilaServerApplication.class, args);
    }
}
