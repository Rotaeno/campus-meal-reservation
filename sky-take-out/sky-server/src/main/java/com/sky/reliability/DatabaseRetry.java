package com.sky.reliability;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionSynchronizationManager;
import java.sql.SQLException;
import java.util.concurrent.ThreadLocalRandom;
import java.util.concurrent.atomic.AtomicLong;

/** Retry only MySQL deadlock/lock timeout vendor codes, never ambiguous connection/commit failures. */
@Component
public class DatabaseRetry {
    private final int attempts;
    private final long baseDelayMs;
    private final AtomicLong retries = new AtomicLong();
    private final AtomicLong exhausted = new AtomicLong();
    public DatabaseRetry(@Value("${sky.database-retry.max-attempts:3}") int attempts,
                         @Value("${sky.database-retry.base-delay-ms:25}") long baseDelayMs) {
        if (attempts < 1 || attempts > 5 || baseDelayMs < 0 || baseDelayMs > 1000) {
            throw new IllegalArgumentException("Invalid bounded DB retry configuration");
        }
        this.attempts = attempts; this.baseDelayMs = baseDelayMs;
    }
    public interface Work<T> { T run() throws Throwable; }
    public <T> T execute(Work<T> work) throws Throwable {
        // An inner REQUIRED operation cannot roll back/restart its caller's transaction.
        if (TransactionSynchronizationManager.isActualTransactionActive()) { return work.run(); }
        for (int attempt = 1; ; attempt++) {
            try { return work.run(); }
            catch (Throwable failure) {
                if (!retryable(failure)) { throw failure; }
                if (attempt >= attempts) { exhausted.incrementAndGet(); throw failure; }
                // TransactionInterceptor/TransactionTemplate has already rolled back here.
                if (TransactionSynchronizationManager.isActualTransactionActive()) { throw failure; }
                retries.incrementAndGet();
                long delay = Math.min(1000, baseDelayMs * (1L << (attempt - 1)));
                try { Thread.sleep(delay + (delay == 0 ? 0 : ThreadLocalRandom.current().nextLong(delay + 1))); }
                catch (InterruptedException interrupted) { Thread.currentThread().interrupt(); throw failure; }
            }
        }
    }
    public <T> T transaction(org.springframework.transaction.support.TransactionTemplate template,
                             org.springframework.transaction.support.TransactionCallback<T> work) {
        try { return execute(() -> template.execute(work)); }
        catch (RuntimeException | Error failure) { throw failure; }
        catch (Throwable failure) { throw new IllegalStateException(failure); }
    }
    static boolean retryable(Throwable failure) {
        // Bound traversal also protects against incorrectly cyclic exception causes.
        for (int depth = 0; failure != null && depth < 20; depth++, failure = failure.getCause()) {
            if (failure instanceof SQLException) {
                SQLException sql = (SQLException) failure;
                if ((sql.getErrorCode() == 1213 && "40001".equals(sql.getSQLState()))
                        || (sql.getErrorCode() == 1205 && ("HY000".equals(sql.getSQLState())
                        || "40001".equals(sql.getSQLState())))) { return true; }
            }
        }
        return false;
    }
    public long getRetries() { return retries.get(); }
    public long getExhausted() { return exhausted.get(); }
}
