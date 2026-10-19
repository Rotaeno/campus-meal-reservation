package com.sky.task;

import com.sky.mapper.OrderMapper;
import com.sky.service.OrderService;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import java.time.Clock;
import java.time.LocalDateTime;
import java.util.*;
import java.util.concurrent.atomic.AtomicLong;
import lombok.extern.slf4j.Slf4j;

@Component @Slf4j
public class OrderTask {
    private final OrderMapper orders;
    private final OrderService service;
    private final Clock clock;
    private final AtomicLong scans = new AtomicLong();
    private final AtomicLong cancelled = new AtomicLong();
    private final AtomicLong failures = new AtomicLong();
    public OrderTask(OrderMapper orders,OrderService service,Clock clock) { this.orders=orders; this.service=service; this.clock=clock; }
    @Scheduled(fixedDelayString="${sky.order.timeout-scan-ms:30000}")
    public void processTimeoutOrder() {
        LocalDateTime now=LocalDateTime.now(clock);
        scans.incrementAndGet();
        for(Long id:orders.findExpired(now.minusMinutes(15),now)) {
            try { if(service.expireOrder(id)) { cancelled.incrementAndGet(); } }
            catch(RuntimeException failure) {
                failures.incrementAndGet();
                log.warn("Order timeout {} failed: {}", id, failure.getClass().getSimpleName());
                // One conflicting/broken row must not stop the rest of this batch; retry on a later scan.
            }
        }
    }
    public Map<String,Object> metrics() {
        Map<String,Object> values = new LinkedHashMap<>();
        values.put("scans", scans.get()); values.put("cancelled", cancelled.get());
        values.put("failures", failures.get()); return values;
    }
    // Delivery and pickup are completed by real actions, not a nightly unconditional status rewrite.
}
