package com.sky.reliability;

import org.aspectj.lang.ProceedingJoinPoint;
import org.aspectj.lang.annotation.Around;
import org.aspectj.lang.annotation.Aspect;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;

@Aspect @Component
@Order(Ordered.LOWEST_PRECEDENCE - 1) // Transaction advisor defaults to LOWEST_PRECEDENCE.
public class DatabaseRetryAspect {
    private final DatabaseRetry retry;
    public DatabaseRetryAspect(DatabaseRetry retry) { this.retry = retry; }
    @Around("@annotation(com.sky.reliability.RetryDatabaseTransaction)")
    public Object retry(ProceedingJoinPoint call) throws Throwable { return retry.execute(call::proceed); }
}
