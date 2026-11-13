package com.aila.server.sync;

/** 某个用户的数据变了，最新序号是 seq。事务提交后才通知各设备，保证它们来拉的时候数据已经在了 */
public record ChangesCommitted(long userId, long seq) {
}
