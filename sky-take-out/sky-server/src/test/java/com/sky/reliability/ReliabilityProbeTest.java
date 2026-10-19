package com.sky.reliability;

import org.junit.jupiter.api.Test;
import org.springframework.mock.env.MockEnvironment;
import org.springframework.transaction.support.TransactionSynchronizationManager;
import static org.junit.jupiter.api.Assertions.*;

class ReliabilityProbeTest {
    @Test void localFaultsAreOffByDefault() {
        MockEnvironment env = new MockEnvironment(); env.setActiveProfiles("local");
        env.setProperty("sky.local.reliability.synchronous-refund-scan", "true");
        ReliabilityProbe probe = new ReliabilityProbe(env, false);
        assertFalse(probe.synchronousRefundScan());
        assertThrows(IllegalArgumentException.class, () -> probe.arm("id", "submit.beforeCommit", "key"));
        probe.submission("key"); // Does not register synchronization or require an active transaction.
        assertFalse(TransactionSynchronizationManager.isSynchronizationActive());
    }
    @Test void enablingPropertyOutsideLocalDoesNotEnableFaults() {
        MockEnvironment env = new MockEnvironment(); env.setActiveProfiles("dev");
        env.setProperty("sky.local.reliability.synchronous-refund-scan", "true");
        ReliabilityProbe probe = new ReliabilityProbe(env, true);
        assertFalse(probe.synchronousRefundScan());
        assertThrows(IllegalArgumentException.class, () -> probe.arm("id", "submit.beforeCommit", "key"));
        probe.hit("submit.beforeCommit", "key");
    }
    @Test void synchronousRefundAblationRequiresBothLocalAndExplicitOptIn() {
        MockEnvironment env = new MockEnvironment(); env.setActiveProfiles("local");
        assertFalse(new ReliabilityProbe(env, true).synchronousRefundScan());
        env.setProperty("sky.local.reliability.synchronous-refund-scan", "true");
        assertTrue(new ReliabilityProbe(env, true).synchronousRefundScan());
    }
}
