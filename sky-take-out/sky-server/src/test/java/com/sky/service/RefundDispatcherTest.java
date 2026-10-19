package com.sky.service;

import org.junit.jupiter.api.Test;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicBoolean;
import static org.junit.jupiter.api.Assertions.*;

class RefundDispatcherTest {
    @Test void queueRejectionDoesNotExecuteWorkOrClaimBeforeWorkerStarts() throws Exception {
        RefundDispatcher dispatcher = new RefundDispatcher(1, 1);
        CountDownLatch entered = new CountDownLatch(1), release = new CountDownLatch(1), queuedDone = new CountDownLatch(1);
        AtomicBoolean queued = new AtomicBoolean(), rejected = new AtomicBoolean();
        try {
            assertTrue(dispatcher.dispatch(1L, () -> { entered.countDown(); await(release); }));
            assertTrue(entered.await(2, TimeUnit.SECONDS));
            assertTrue(dispatcher.dispatch(2L, () -> { queued.set(true); queuedDone.countDown(); }));
            assertFalse(dispatcher.dispatch(2L, () -> fail("Duplicate admission")));
            assertFalse(dispatcher.dispatch(3L, () -> rejected.set(true)));
            assertFalse(queued.get()); assertFalse(rejected.get());
            assertEquals(1, dispatcher.metrics().get("queued"));
            release.countDown(); assertTrue(queuedDone.await(2, TimeUnit.SECONDS));
            assertTrue(queued.get()); assertFalse(rejected.get());
        } finally { release.countDown(); dispatcher.destroy(); }
    }
    @Test void failedWorkerDoesNotStopTheNextTask() throws Exception {
        RefundDispatcher dispatcher = new RefundDispatcher(1, 1);
        CountDownLatch done = new CountDownLatch(1);
        try {
            dispatcher.dispatch(1L, () -> { throw new IllegalStateException("DB claim failed"); });
            dispatcher.dispatch(2L, done::countDown);
            assertTrue(done.await(2, TimeUnit.SECONDS));
            assertEquals(1L, dispatcher.metrics().get("failures"));
        } finally { dispatcher.destroy(); }
    }
    private static void await(CountDownLatch latch) {
        try { latch.await(); } catch (InterruptedException error) { Thread.currentThread().interrupt(); }
    }
}
