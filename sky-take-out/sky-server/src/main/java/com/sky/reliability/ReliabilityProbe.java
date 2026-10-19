package com.sky.reliability;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.core.env.Environment;
import org.springframework.core.env.Profiles;
import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;
import java.util.*;
import java.util.concurrent.*;

/** Opt-in local one-shot barriers. The test runner, never this class, stops its own process. */
@Component
public class ReliabilityProbe {
    private final boolean enabled;
    private final boolean synchronousRefundScan;
    private final Map<String, Gate> gates = new ConcurrentHashMap<>();
    private static final Set<String> STAGES = new HashSet<>(Arrays.asList(
            "submit.beforeCommit", "submit.afterCommit", "refund.providerConfirmed", "refund.beforeCommit", "dbProbe.afterWrite",
            "payment.providerRecorded"));
    public ReliabilityProbe(Environment environment, @Value("${sky.local.reliability.enabled:false}") boolean enabled) {
        this.enabled = enabled && environment.acceptsProfiles(Profiles.of("local"));
        // Ablation only: never reactivate synchronous provider work in a normal deployment.
        this.synchronousRefundScan = this.enabled && environment.getProperty(
                "sky.local.reliability.synchronous-refund-scan", Boolean.class, false);
    }
    public boolean synchronousRefundScan() { return synchronousRefundScan; }
    public static class Gate {
        public final String stage;
        public final String key;
        public volatile boolean reached;
        public volatile boolean finished;
        private final CountDownLatch release = new CountDownLatch(1);
        Gate(String stage, String key) { this.stage = stage; this.key = key; }
    }
    public void arm(String id, String stage, String key) {
        if (!enabled || !STAGES.contains(stage) || id == null || !id.matches("[A-Za-z0-9_-]{1,64}")
                || key == null || key.length() > 100) { throw new IllegalArgumentException("Invalid local probe"); }
        if (gates.size() >= 100 || gates.putIfAbsent(id, new Gate(stage, key)) != null) {
            throw new IllegalArgumentException("Probe already exists or limit reached");
        }
    }
    public Gate get(String id) { return gates.get(id); }
    public void release(String id) { Gate gate = gates.get(id); if (gate != null) { gate.release.countDown(); } }
    public void hit(String stage, String key) {
        if (!enabled) { return; }
        for (Gate gate : gates.values()) {
            if (!gate.stage.equals(stage) || !gate.key.equals(key)) { continue; }
            synchronized (gate) { if (gate.reached) { continue; } gate.reached = true; }
            try {
                if (!gate.release.await(45, TimeUnit.SECONDS)) { throw new IllegalStateException("Local probe timed out"); }
            } catch (InterruptedException interrupted) {
                Thread.currentThread().interrupt(); throw new IllegalStateException("Local probe interrupted", interrupted);
            } finally { gate.finished = true; }
        }
    }
    public void submission(String key) {
        if (!enabled) { return; }
        TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
            @Override public void beforeCommit(boolean readOnly) { hit("submit.beforeCommit", key); }
            @Override public void afterCommit() { hit("submit.afterCommit", key); }
        });
    }
    public void refundCommit(String key) {
        if (!enabled) { return; }
        TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
            @Override public void beforeCommit(boolean readOnly) { hit("refund.beforeCommit", key); }
        });
    }
}
