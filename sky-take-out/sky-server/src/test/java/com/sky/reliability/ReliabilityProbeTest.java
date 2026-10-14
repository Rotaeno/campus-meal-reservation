package com.sky.reliability;

import org.junit.jupiter.api.Test;
import org.springframework.mock.env.MockEnvironment;
import org.springframework.transaction.support.TransactionSynchronizationManager;
import static org.junit.jupiter.api.Assertions.*;

class ReliabilityProbeTest {
    @Test void localFaultsAreOffByDefault() {
        MockEnvironment env = new MockEnvironment(); env.setActiveProfiles("local");
        ReliabilityProbe probe = new ReliabilityProbe(env, false);
        assertThrows(IllegalArgumentException.class, () -> probe.arm("id", "submit.beforeCommit", "key"));
        probe.submission("key"); // Does not register synchronization or require an active transaction.
        assertFalse(TransactionSynchronizationManager.isSynchronizationActive());
    }
    @Test void enablingPropertyOutsideLocalDoesNotEnableFaults() {
        MockEnvironment env = new MockEnvironment(); env.setActiveProfiles("dev");
        ReliabilityProbe probe = new ReliabilityProbe(env, true);
        assertThrows(IllegalArgumentException.class, () -> probe.arm("id", "submit.beforeCommit", "key"));
        probe.hit("submit.beforeCommit", "key");
    }
}
