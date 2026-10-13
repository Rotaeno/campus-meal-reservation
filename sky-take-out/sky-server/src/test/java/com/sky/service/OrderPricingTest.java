package com.sky.service;

import com.sky.entity.*;
import com.sky.exception.OrderBusinessException;
import com.sky.mapper.DishMapper;
import com.sky.mapper.SetmealMapper;
import org.junit.jupiter.api.*;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.*;
import org.mockito.junit.jupiter.MockitoExtension;
import java.math.BigDecimal;
import java.util.Arrays;
import java.util.Collections;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class OrderPricingTest {
    @Mock private DishMapper dishes;
    @Mock private SetmealMapper setmeals;
    private OrderPricingService pricing;
    @BeforeEach void setup() { pricing=new OrderPricingService(dishes,setmeals,1,new BigDecimal("6.00")); }
    private ShoppingCart cart(int count) { return ShoppingCart.builder().dishId(1L).number(count)
            .amount(new BigDecimal("0.01")).name("tampered").build(); }
    private void available() { when(dishes.getForPricing(1L)).thenReturn(Dish.builder().id(1L).name("标准午餐")
            .price(new BigDecimal("10.10")).status(1).build()); }
    @Test void serverCatalogAndFeesOverrideTheCartPriceAndName() {
        available(); OrderPricingService.Quote quote=pricing.quote(Collections.singletonList(cart(3)),false);
        assertEquals(new BigDecimal("39.30"),quote.getAmount()); assertEquals(3,quote.getPackAmount());
        assertEquals("标准午餐",quote.getDetails().get(0).getName()); assertEquals(new BigDecimal("10.10"),quote.getDetails().get(0).getAmount());
    }
    @Test void pickupDoesNotChargeDelivery() {
        available(); OrderPricingService.Quote quote=pricing.quote(Collections.singletonList(cart(1)),true);
        assertEquals(new BigDecimal("11.10"),quote.getAmount()); assertEquals(new BigDecimal("0.00"),quote.getDeliveryFee());
    }
    @Test void unavailableProductCannotBeOrdered() {
        when(dishes.getForPricing(1L)).thenReturn(Dish.builder().status(0).build());
        assertThrows(OrderBusinessException.class,()->pricing.quote(Collections.singletonList(cart(1)),true));
    }
    @Test void negativeQuantityCannotReduceTheBill() { assertThrows(OrderBusinessException.class,()->pricing.priceItem(cart(-2))); }
    @Test void aRowCannotNameBothADishAndASetmeal() {
        ShoppingCart invalid=cart(1); invalid.setSetmealId(2L);
        assertThrows(OrderBusinessException.class,()->pricing.priceItem(invalid));
    }
    @Test void combinedQuantityIsBounded() {
        available(); assertThrows(OrderBusinessException.class,()->pricing.quote(Arrays.asList(cart(99),cart(99),cart(3)),true));
    }
    @Test void anEmptyCartCannotCreateAnOrder() { assertThrows(OrderBusinessException.class,()->pricing.quote(Collections.emptyList(),true)); }
}
