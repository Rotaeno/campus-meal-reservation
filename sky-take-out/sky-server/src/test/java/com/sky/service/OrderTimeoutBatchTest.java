package com.sky.service;

import com.sky.mapper.OrderMapper;
import com.sky.task.OrderTask;
import org.junit.jupiter.api.Test;
import java.time.Clock;
import java.util.Arrays;
import static org.mockito.Mockito.*;
import static org.mockito.ArgumentMatchers.any;
import static org.junit.jupiter.api.Assertions.*;

class OrderTimeoutBatchTest {
    @Test void oneFailedOrderDoesNotAbortOtherExpiredOrders() {
        OrderMapper mapper = mock(OrderMapper.class); OrderService service = mock(OrderService.class);
        when(mapper.findExpired(any(), any())).thenReturn(Arrays.asList(1L, 2L));
        when(service.expireOrder(1L)).thenThrow(new IllegalStateException("broken row"));
        when(service.expireOrder(2L)).thenReturn(true);
        OrderTask task = new OrderTask(mapper, service, Clock.systemUTC());
        task.processTimeoutOrder();
        verify(service).expireOrder(2L);
        assertEquals(1L, task.metrics().get("failures")); assertEquals(1L, task.metrics().get("cancelled"));
    }
}
