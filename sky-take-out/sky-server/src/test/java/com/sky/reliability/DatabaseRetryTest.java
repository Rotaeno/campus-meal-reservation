package com.sky.reliability;

import org.junit.jupiter.api.*;
import org.springframework.context.annotation.*;
import org.springframework.dao.CannotAcquireLockException;
import org.springframework.transaction.*;
import org.springframework.transaction.annotation.*;
import org.springframework.transaction.support.*;
import java.sql.SQLException;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;

/** Exercises actual Spring proxy ordering, transaction begin/rollback/commit and nested behavior. */
class DatabaseRetryTest {
    AnnotationConfigApplicationContext context;
    Operation operation;
    Manager manager;
    @BeforeEach void open() {
        context = new AnnotationConfigApplicationContext(Config.class);
        operation = context.getBean(Operation.class); manager = context.getBean(Manager.class);
    }
    @AfterEach void close() { context.close(); }
    @Test void deadlockRestartsTheWholeTransactionWithTheSameKey() {
        operation.configure(1, 1213, "40001");
        assertEquals("original-key", operation.submit("original-key"));
        assertEquals(2, manager.begun.size()); assertNotSame(manager.begun.get(0), manager.begun.get(1));
        assertEquals(1, manager.rollbacks); assertEquals(1, manager.commits);
        assertEquals(Collections.singletonList("original-key"), manager.persisted);
        assertEquals(Arrays.asList("original-key", "original-key"), operation.keys());
        assertEquals(1, context.getBean(DatabaseRetry.class).getRetries());
    }
    @Test void lockTimeoutAlsoRollsBackPriorWritesBeforeRetry() {
        operation.configure(1, 1205, "40001"); operation.submit("timeout-key");
        assertEquals(1, manager.rollbacks); assertEquals(Collections.singletonList("timeout-key"), manager.persisted);
    }
    @Test void retriesAreBoundedAndAllFailedWritesRollBack() {
        operation.configure(10, 1213, "40001");
        assertThrows(CannotAcquireLockException.class, () -> operation.submit("bounded-key"));
        assertEquals(3, manager.rollbacks); assertEquals(0, manager.commits); assertTrue(manager.persisted.isEmpty());
        assertEquals(1, context.getBean(DatabaseRetry.class).getExhausted());
    }
    @Test void connectionFailureWithUnknownCommitOutcomeIsNotRetried() {
        operation.configure(1, 2013, "08S01");
        assertThrows(CannotAcquireLockException.class, () -> operation.submit("unknown-key"));
        assertEquals(1, manager.begun.size());
    }
    @Test void duplicateKeyAndBusinessFailureAreNotRetried() {
        operation.configure(1, 1062, "23000");
        assertThrows(CannotAcquireLockException.class, () -> operation.submit("duplicate-key"));
        assertEquals(1, manager.begun.size());
        assertFalse(DatabaseRetry.retryable(new IllegalArgumentException("business rejection")));
    }
    @Test void anOperationJoiningAnOuterTransactionDoesNotRestartItsCaller() {
        operation.configure(1, 1213, "40001");
        assertThrows(CannotAcquireLockException.class, () -> new TransactionTemplate(manager).execute(status -> operation.submit("nested-key")));
        assertEquals(1, operation.keys().size()); assertEquals(1, manager.rollbacks); assertTrue(manager.persisted.isEmpty());
    }
    @Test void vendorCodeWithoutExpectedSqlStateIsNotRetried() {
        assertFalse(DatabaseRetry.retryable(new SQLException("mismatch", "08S01", 1213)));
        assertFalse(DatabaseRetry.retryable(new SQLException("mismatch", "08S01", 1205)));
    }
    @Test void rawServerLockTimeoutStateAndConnectorJMappingAreBothAccepted() {
        assertTrue(DatabaseRetry.retryable(new SQLException("server", "HY000", 1205)));
        assertTrue(DatabaseRetry.retryable(new SQLException("jdbc", "40001", 1205)));
    }
    @Configuration @EnableAspectJAutoProxy @EnableTransactionManagement
    static class Config {
        @Bean DatabaseRetry retry() { return new DatabaseRetry(3, 0); }
        @Bean DatabaseRetryAspect aspect(DatabaseRetry retry) { return new DatabaseRetryAspect(retry); }
        @Bean Manager transactionManager() { return new Manager(); }
        @Bean Operation operation(Manager manager) { return new Operation(manager); }
    }
    static class Operation {
        final Manager manager;
        final List<String> attempted = new ArrayList<>();
        int failures, code; String state;
        Operation(Manager manager) { this.manager = manager; }
        public void configure(int failures, int code, String state) { this.failures = failures; this.code = code; this.state = state; }
        public List<String> keys() { return attempted; }
        @RetryDatabaseTransaction @Transactional(isolation=Isolation.READ_COMMITTED)
        public String submit(String key) {
            assertTrue(TransactionSynchronizationManager.isActualTransactionActive());
            attempted.add(key); manager.current.get().pending.add(key);
            if (failures-- > 0) { throw new CannotAcquireLockException("probe", new SQLException("probe", state, code)); }
            return key;
        }
    }
    static class Tx { boolean active; final List<String> pending = new ArrayList<>(); }
    static class Manager extends AbstractPlatformTransactionManager {
        final ThreadLocal<Tx> current = new ThreadLocal<>();
        final List<Tx> begun = new ArrayList<>(); final List<String> persisted = new ArrayList<>();
        int commits, rollbacks;
        protected Object doGetTransaction() { return current.get() == null ? new Tx() : current.get(); }
        protected boolean isExistingTransaction(Object tx) { return ((Tx) tx).active; }
        protected void doBegin(Object tx, TransactionDefinition definition) { ((Tx) tx).active = true; current.set((Tx) tx); begun.add((Tx) tx); }
        protected void doCommit(DefaultTransactionStatus status) { persisted.addAll(((Tx) status.getTransaction()).pending); commits++; }
        protected void doRollback(DefaultTransactionStatus status) { ((Tx) status.getTransaction()).pending.clear(); rollbacks++; }
        protected void doSetRollbackOnly(DefaultTransactionStatus status) { }
        protected void doCleanupAfterCompletion(Object tx) { current.remove(); }
    }
}
