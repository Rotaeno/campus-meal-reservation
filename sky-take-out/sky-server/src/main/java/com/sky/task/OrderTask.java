package com.sky.task;

import com.sky.mapper.OrderMapper;
import com.sky.service.OrderService;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import java.time.Clock;
import java.time.LocalDateTime;

@Component
public class OrderTask {
    private final OrderMapper orders;
    private final OrderService service;
    private final Clock clock;
    public OrderTask(OrderMapper orders,OrderService service,Clock clock) { this.orders=orders; this.service=service; this.clock=clock; }
    @Scheduled(fixedDelayString="${sky.order.timeout-scan-ms:30000}")
    public void processTimeoutOrder() {
        LocalDateTime now=LocalDateTime.now(clock);
        for(Long id:orders.findExpired(now.minusMinutes(15),now)) { service.expireOrder(id); }
    }
    // Delivery and pickup are completed by real actions, not a nightly unconditional status rewrite.
}
