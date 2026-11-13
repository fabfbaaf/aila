package com.aila.server.common;

import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.method.annotation.HandlerMethodValidationException;

/** 所有错误都返回同一种格式：{"code": "...", "message": "..."} */
@RestControllerAdvice
public class ApiErrorHandler {

    public record ErrorBody(String code, String message) {
    }

    @ExceptionHandler(ApiException.class)
    ResponseEntity<ErrorBody> api(ApiException e) {
        return ResponseEntity.status(e.status()).body(new ErrorBody(e.code(), e.getMessage()));
    }

    @ExceptionHandler(MethodArgumentNotValidException.class)
    ResponseEntity<ErrorBody> invalid(MethodArgumentNotValidException e) {
        var field = e.getBindingResult().getFieldError();
        var message = field == null ? "请求参数不对" : field.getField() + " " + field.getDefaultMessage();
        return badRequest(message);
    }

    @ExceptionHandler(HandlerMethodValidationException.class)
    ResponseEntity<ErrorBody> invalidParam(HandlerMethodValidationException e) {
        return badRequest("请求参数不对");
    }

    @ExceptionHandler(HttpMessageNotReadableException.class)
    ResponseEntity<ErrorBody> unreadable(HttpMessageNotReadableException e) {
        return badRequest("请求内容不是合法的 JSON");
    }

    private static ResponseEntity<ErrorBody> badRequest(String message) {
        return ResponseEntity.status(HttpStatus.BAD_REQUEST).body(new ErrorBody("VALIDATION", message));
    }
}
