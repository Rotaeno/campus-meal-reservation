package com.sky.service;

import com.sky.context.BaseContext;
import com.sky.entity.Orders;
import com.sky.exception.OrderBusinessException;
import com.sky.mapper.OrderMapper;
import com.sky.service.impl.OrderServiceImpl;
import com.sky.utils.WeChatPayUtil;
import com.sky.websocket.WebSocketServer;
import org.junit.jupiter.api.*;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.*;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.test.util.ReflectionTestUtils;
import java.math.BigDecimal;
import java.time.*;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class OrderServiceRegressionTest {
    @Mock private OrderMapper orderMapper;
    @Mock private WeChatPayUtil payment;
    @Mock private RefundService refunds;
    @Mock private WebSocketServer webSocketServer;
    @InjectMocks private OrderServiceImpl service;
    private final Clock clock=Clock.fixed(Instant.parse("2026-10-11T04:00:00Z"),ZoneId.of("Asia/Shanghai"));
    @BeforeEach void setup() { ReflectionTestUtils.setField(service,"clock",clock); BaseContext.setCurrentId(1L); }
    @AfterEach void clear() { BaseContext.removeCurrentId(); }
    private Orders order(int status,int pay) { return Orders.builder().id(42L).userId(1L).number("demo-42")
            .status(status).payStatus(pay).fulfillmentType(1).amount(new BigDecimal("32.50"))
            .orderTime(LocalDateTime.now(clock)).build(); }

    @Test void deliveryCanBecomeCompletedWithItsOriginalId() {
        when(orderMapper.getById(42L)).thenReturn(order(4,1));
        when(orderMapper.transition(eq(42L),eq(4),eq(5),any())).thenReturn(1);
        service.complete(42L);
        verify(orderMapper).transition(42L,4,5,LocalDateTime.now(clock));
    }
    @Test void pendingOrderCannotSkipDeliveryAndBecomeCompleted() {
        when(orderMapper.getById(42L)).thenReturn(order(2,1));
        assertThrows(OrderBusinessException.class,()->service.complete(42L));
    }
    @Test void missingOrderCannotBeCompleted() {
        assertThrows(OrderBusinessException.class,()->service.complete(42L));
        verify(orderMapper,never()).transition(any(),any(),any(),any());
    }
    @Test void paidCancellationQueuesThePersistedAmountWithoutCallingProviderInsideTransaction() {
        when(orderMapper.getByIdForUpdate(42L)).thenReturn(order(2,1));
        when(orderMapper.cancelIfMatches(eq(42L),eq(2),eq(1),anyString(),any())).thenReturn(1);
        service.userCancelById(42L);
        ArgumentCaptor<Orders> intent=ArgumentCaptor.forClass(Orders.class);
        verify(refunds).enqueue(intent.capture()); assertEquals(new BigDecimal("32.50"),intent.getValue().getAmount());
        verifyNoInteractions(payment);
    }
    @Test void unpaidCancellationDoesNotRequestARefund() {
        when(orderMapper.getByIdForUpdate(42L)).thenReturn(order(1,0));
        when(orderMapper.cancelIfMatches(eq(42L),eq(1),eq(0),anyString(),any())).thenReturn(1);
        service.userCancelById(42L); verifyNoInteractions(payment,refunds);
    }
    @Test void anotherUserCannotCancelTheOrder() {
        Orders owned=order(1,0); owned.setUserId(2L); when(orderMapper.getByIdForUpdate(42L)).thenReturn(owned);
        assertThrows(OrderBusinessException.class,()->service.userCancelById(42L));
        verify(orderMapper,never()).cancelIfMatches(any(),any(),any(),any(),any());
    }
    @Test void verifiedCallbackDoesNotDependOnARequestUserIdentity() {
        BaseContext.removeCurrentId(); when(orderMapper.getByNumberForUpdate("demo-42")).thenReturn(order(1,0));
        when(orderMapper.markPaid(eq(42L),any())).thenReturn(1);
        service.paySuccess("demo-42",new BigDecimal("32.50"));
        verify(orderMapper).markPaid(42L,LocalDateTime.now(clock)); verify(webSocketServer).sendToAllClient(anyString());
    }
    @Test void duplicatePaymentDoesNotEmitAnotherEvent() {
        when(orderMapper.getByNumberForUpdate("demo-42")).thenReturn(order(2,1));
        service.paySuccess("demo-42",new BigDecimal("32.50")); verifyNoInteractions(webSocketServer);
        verify(orderMapper,never()).markPaid(any(),any());
    }
    @Test void aWrongPaymentAmountDoesNotChangeTheOrder() {
        when(orderMapper.getByNumberForUpdate("demo-42")).thenReturn(order(1,0));
        assertThrows(OrderBusinessException.class,()->service.paySuccess("demo-42",new BigDecimal("0.01")));
        verify(orderMapper,never()).markPaid(any(),any());
    }
    @Test void pickupOrdersCannotBypassRedemptionThroughDeliveryComplete() {
        Orders pickup=order(4,1); pickup.setFulfillmentType(2); when(orderMapper.getById(42L)).thenReturn(pickup);
        assertThrows(OrderBusinessException.class,()->service.complete(42L));
        verify(orderMapper,never()).transition(any(),any(),any(),any());
    }
}
