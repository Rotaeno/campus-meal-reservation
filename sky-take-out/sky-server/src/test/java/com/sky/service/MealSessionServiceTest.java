package com.sky.service;

import com.sky.entity.MealSession;
import com.sky.entity.Orders;
import com.sky.exception.OrderBusinessException;
import com.sky.mapper.MealSessionMapper;
import com.sky.mapper.OrderMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import java.time.*;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

class MealSessionServiceTest {
    private final MealSessionMapper sessions=mock(MealSessionMapper.class);
    private final OrderMapper orders=mock(OrderMapper.class);
    private final LocalDateTime now=LocalDateTime.of(2026,10,12,12,0);
    private final Clock clock=Clock.fixed(now.atZone(ZoneId.of("Asia/Shanghai")).toInstant(),ZoneId.of("Asia/Shanghai"));
    private final MealSessionService service=new MealSessionService(sessions,orders,clock);
    private final String code="abcdefghijklmnopqrstuv";
    private Orders order;
    private MealSession session;
    @BeforeEach void setup() {
        order=new Orders(); order.setId(9L); order.setFulfillmentType(2); order.setMealSessionId(3L);
        session=new MealSession(); session.setId(3L); session.setPickupStart(now.minusMinutes(30)); session.setPickupEnd(now.plusHours(1));
        when(orders.getByPickupCodeForUpdate(code)).thenReturn(order);
        when(sessions.getById(3L)).thenReturn(session);
        when(orders.getById(9L)).thenReturn(order);
    }
    @Test void redeemWithinWindowUsesConditionalTransition() {
        when(orders.transition(9L,4,5,now)).thenReturn(1);
        assertSame(order,service.redeem(code));
        verify(orders).transition(9L,4,5,now);
    }
    @Test void tooEarlyDoesNotAttemptTransition() {
        session.setPickupStart(now.plusSeconds(1));
        assertThrows(OrderBusinessException.class,()->service.redeem(code));
        verify(orders,never()).transition(anyLong(),anyInt(),anyInt(),any());
    }
    @Test void tooLateDoesNotAttemptTransition() {
        session.setPickupEnd(now.minusSeconds(1));
        assertThrows(OrderBusinessException.class,()->service.redeem(code));
        verify(orders,never()).transition(anyLong(),anyInt(),anyInt(),any());
    }
    @Test void repeatedRedemptionFailsOnConditionalUpdate() {
        when(orders.transition(9L,4,5,now)).thenReturn(0);
        assertThrows(OrderBusinessException.class,()->service.redeem(code));
    }
    @Test void invalidCodeNeverQueriesDatabase() {
        assertThrows(OrderBusinessException.class,()->service.redeem("invalid"));
        verify(orders,never()).getByPickupCodeForUpdate(anyString());
    }
    @Test void deliveryOrderCannotUsePickupCode() {
        order.setFulfillmentType(1);
        assertThrows(OrderBusinessException.class,()->service.redeem(code));
        verify(orders,never()).transition(anyLong(),anyInt(),anyInt(),any());
    }
    @Test void createClearsInjectedCountersAndIdentifier() {
        session.setId(999L); session.setReservedCount(999); session.setStatus(0); session.setTitle("午餐");
        session.setCapacity(10); session.setMealDate(now.toLocalDate()); session.setCutoffTime(now.plusHours(1));
        session.setPickupStart(now.plusHours(2)); session.setPickupEnd(now.plusHours(3));
        service.create(session);
        assertNull(session.getId()); assertEquals(0,session.getReservedCount()); assertEquals(1,session.getStatus());
    }
    @Test void cannotCreateAlreadyClosedSession() {
        session.setTitle("午餐"); session.setCapacity(10); session.setMealDate(now.toLocalDate()); session.setCutoffTime(now);
        assertThrows(OrderBusinessException.class,()->service.create(session));
        verify(sessions,never()).insert(any());
    }
}
