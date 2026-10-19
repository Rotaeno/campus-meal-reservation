package com.sky.service;

import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.DisposableBean;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicLong;

/** Bounded local admission, not a distributed lock. The durable task is claimed by its worker. */
@Component @Slf4j
public class RefundDispatcher implements DisposableBean {
    private final ThreadPoolExecutor executor;
    private final Set<Long> admitted = ConcurrentHashMap.newKeySet();
    private final AtomicLong rejected = new AtomicLong();
    private final AtomicLong failures = new AtomicLong();
    public RefundDispatcher(@Value("${sky.refund.workers:2}") int workers,
                            @Value("${sky.refund.queue-capacity:16}") int capacity) {
        if (workers < 1 || workers > 16 || capacity < 1 || capacity > 200) {
            throw new IllegalArgumentException("Invalid bounded refund executor configuration");
        }
        AtomicLong sequence = new AtomicLong();
        executor = new ThreadPoolExecutor(workers, workers, 0, TimeUnit.MILLISECONDS,
                new ArrayBlockingQueue<>(capacity), work -> {
                    Thread thread = new Thread(work, "refund-worker-" + sequence.incrementAndGet());
                    thread.setDaemon(true); return thread;
                }, new ThreadPoolExecutor.AbortPolicy());
    }
    public boolean dispatch(Long id, Runnable work) {
        if (!admitted.add(id)) { return false; }
        try {
            executor.execute(() -> {
                try { work.run(); }
                catch (RuntimeException failure) {
                    failures.incrementAndGet();
                    log.warn("Refund worker {} failed: {}", id, failure.getClass().getSimpleName());
                } finally { admitted.remove(id); }
            });
            return true;
        } catch (RejectedExecutionException full) {
            admitted.remove(id); rejected.incrementAndGet();
            // No claim has happened. A later scan or another instance can still pick up the DB task.
            return false;
        }
    }
    public Map<String,Object> metrics() {
        Map<String,Object> values = new LinkedHashMap<>();
        values.put("workers", executor.getMaximumPoolSize()); values.put("active", executor.getActiveCount());
        values.put("queued", executor.getQueue().size()); values.put("admitted", admitted.size());
        values.put("completed", executor.getCompletedTaskCount()); values.put("rejected", rejected.get());
        values.put("failures", failures.get()); return values;
    }
    @Override public void destroy() {
        executor.shutdown();
        try {
            if (!executor.awaitTermination(3, TimeUnit.SECONDS)) { executor.shutdownNow(); }
        } catch (InterruptedException interrupted) {
            executor.shutdownNow(); Thread.currentThread().interrupt();
        }
        // Queued work was never claimed. In-progress claims remain recoverable by their DB lease.
    }
}
