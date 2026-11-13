package com.aila.server.common;

import org.springframework.http.HttpStatus;

/** 业务错误：code 给程序判断，message 给人看（桌面端可以直接显示） */
public class ApiException extends RuntimeException {

    private final HttpStatus status;
    private final String code;

    public ApiException(HttpStatus status, String code, String message) {
        super(message);
        this.status = status;
        this.code = code;
    }

    public HttpStatus status() {
        return status;
    }

    public String code() {
        return code;
    }
}
