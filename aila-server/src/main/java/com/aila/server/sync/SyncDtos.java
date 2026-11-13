package com.aila.server.sync;

import java.time.Instant;
import java.util.List;
import java.util.UUID;

import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotEmpty;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;

import tools.jackson.databind.JsonNode;

/** 同步接口的请求和响应 */
public final class SyncDtos {

    private SyncDtos() {
    }

    /** id 由客户端生成：同一条消息重传多少次都只存一份 */
    public record NewMessage(
            @NotNull UUID id,
            @NotNull @Pattern(regexp = "user|assistant") String role,
            @NotNull @Size(max = 8000) String content,
            @NotNull Instant createdAt) {
    }

    public record UploadRequest(@NotEmpty @Size(max = 100) List<@Valid NewMessage> messages) {
    }

    public record Accepted(UUID id, long seq) {
    }

    public record UploadResponse(List<Accepted> accepted, long lastSeq) {
    }

    public record SettingRequest(@NotNull JsonNode value) {
    }

    /** 新增或修改一条记忆（id 由客户端生成） */
    public record MemoryRequest(
            @NotBlank @Size(max = 500) String content,
            @NotNull @Pattern(regexp = "profile|preference|event|other") String kind,
            Instant expiresAt,
            @NotNull Instant createdAt) {
    }

    public record MemoryData(UUID id, String content, String kind, Instant expiresAt, Instant createdAt,
            Instant updatedAt, boolean deleted) {
    }

    public record SeqResponse(long seq) {
    }

    // ---------- 拉取变更 ----------

    public record MessageData(UUID id, String role, String content, Instant createdAt, String deviceId, boolean deleted) {
    }

    public record SettingData(String key, JsonNode value, boolean deleted) {
    }

    /** type 是 message、setting 或 memory，data 对应 MessageData、SettingData、MemoryData */
    public record Change(long seq, String type, Object data) {
    }

    /**
     * nextSince：下次从这里接着拉；hasMore：这一页没拉完，马上再拉一次；
     * lastSeq：服务端当前最新的序号
     */
    public record SyncResponse(List<Change> changes, long nextSince, boolean hasMore, long lastSeq) {
    }
}
