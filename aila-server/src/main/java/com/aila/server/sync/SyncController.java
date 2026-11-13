package com.aila.server.sync;

import java.util.UUID;

import jakarta.validation.Valid;

import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import com.aila.server.auth.TokenService;
import com.aila.server.sync.SyncDtos.MemoryRequest;
import com.aila.server.sync.SyncDtos.SeqResponse;
import com.aila.server.sync.SyncDtos.SettingRequest;
import com.aila.server.sync.SyncDtos.SyncResponse;
import com.aila.server.sync.SyncDtos.UploadRequest;
import com.aila.server.sync.SyncDtos.UploadResponse;

@RestController
@RequestMapping("/api")
public class SyncController {

    private final SyncService sync;

    public SyncController(SyncService sync) {
        this.sync = sync;
    }

    /** 拉取 since 之后的所有变更（消息和设置），按序号排好 */
    @GetMapping("/sync")
    SyncResponse changes(@AuthenticationPrincipal Jwt jwt,
            @RequestParam(defaultValue = "0") long since,
            @RequestParam(defaultValue = "200") int limit) {
        return sync.changes(userId(jwt), Math.max(0, since), limit);
    }

    /** 上传消息（可以一次多条）。重复上传是安全的，返回每条消息的序号 */
    @PostMapping("/messages")
    UploadResponse upload(@AuthenticationPrincipal Jwt jwt, @Valid @RequestBody UploadRequest r) {
        return sync.saveMessages(userId(jwt), jwt.getClaimAsString(TokenService.DEVICE_CLAIM), r.messages());
    }

    @DeleteMapping("/messages/{id}")
    SeqResponse delete(@AuthenticationPrincipal Jwt jwt, @PathVariable UUID id) {
        return new SeqResponse(sync.deleteMessage(userId(jwt), id));
    }

    /** 新增或修改一条记忆。已删除的记忆返回 410，客户端应放弃本地的修改 */
    @PutMapping("/memories/{id}")
    SeqResponse putMemory(@AuthenticationPrincipal Jwt jwt, @PathVariable UUID id, @Valid @RequestBody MemoryRequest r) {
        return new SeqResponse(sync.putMemory(userId(jwt), id, r));
    }

    @DeleteMapping("/memories/{id}")
    SeqResponse deleteMemory(@AuthenticationPrincipal Jwt jwt, @PathVariable UUID id) {
        return new SeqResponse(sync.deleteMemory(userId(jwt), id));
    }

    @PutMapping("/settings/{key}")
    SeqResponse putSetting(@AuthenticationPrincipal Jwt jwt, @PathVariable String key, @Valid @RequestBody SettingRequest r) {
        return new SeqResponse(sync.putSetting(userId(jwt), key, r.value()));
    }

    @DeleteMapping("/settings/{key}")
    SeqResponse deleteSetting(@AuthenticationPrincipal Jwt jwt, @PathVariable String key) {
        return new SeqResponse(sync.deleteSetting(userId(jwt), key));
    }

    private static long userId(Jwt jwt) {
        return Long.parseLong(jwt.getSubject());
    }
}
