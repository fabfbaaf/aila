package com.aila.server.sync;

import org.springframework.stereotype.Component;
import org.springframework.transaction.event.TransactionPhase;
import org.springframework.transaction.event.TransactionalEventListener;

/**
 * 事务提交之后才推送。如果在事务里就推，客户端收到通知立刻来拉，可能数据还没提交，拉了个空，
 * 而之后不会再有通知，这条变更就要等到下次才同步过去。
 */
@Component
class SyncNotifier {

    private final SyncSocketHandler sockets;

    SyncNotifier(SyncSocketHandler sockets) {
        this.sockets = sockets;
    }

    @TransactionalEventListener(phase = TransactionPhase.AFTER_COMMIT)
    void onCommitted(ChangesCommitted e) {
        sockets.notifyChanged(e.userId(), e.seq());
    }
}
