package com.sky.service;

import com.sky.entity.*;
import com.sky.exception.OrderBusinessException;
import com.sky.mapper.DishMapper;
import com.sky.mapper.SetmealMapper;
import lombok.AllArgsConstructor;
import lombok.Data;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.util.*;

@Service
public class OrderPricingService {
    private final DishMapper dishes;
    private final SetmealMapper setmeals;
    private final int packagingPerItem;
    private final BigDecimal deliveryFee;

    public OrderPricingService(DishMapper dishes, SetmealMapper setmeals,
                               @Value("${sky.order.packaging-fee-per-item:1}") int packagingPerItem,
                               @Value("${sky.order.delivery-fee:6.00}") BigDecimal deliveryFee) {
        this.dishes = dishes;
        this.setmeals = setmeals;
        if (packagingPerItem < 0 || packagingPerItem > 20 || deliveryFee.signum() < 0) {
            throw new IllegalArgumentException("Invalid server-side fee configuration");
        }
        this.packagingPerItem = packagingPerItem;
        this.deliveryFee = deliveryFee.setScale(2);
    }

    /** Shared locking reads keep checkout prices stable until the order commits. */
    public OrderDetail priceItem(ShoppingCart cart) {
        if ((cart.getDishId() == null) == (cart.getSetmealId() == null)
                || cart.getNumber() == null || cart.getNumber() < 1 || cart.getNumber() > 99) {
            throw new OrderBusinessException("商品或数量不合法");
        }
        OrderDetail result = new OrderDetail();
        result.setDishId(cart.getDishId());
        result.setSetmealId(cart.getSetmealId());
        result.setNumber(cart.getNumber());
        String flavor = cart.getDishFlavor() == null ? "" : cart.getDishFlavor();
        if (flavor.length() > 50) { throw new OrderBusinessException("口味说明过长"); }
        result.setDishFlavor(flavor);
        if (cart.getDishId() != null) {
            Dish dish = dishes.getForPricing(cart.getDishId());
            if (dish == null || !Integer.valueOf(1).equals(dish.getStatus())) {
                throw new OrderBusinessException("菜品不存在或已停售");
            }
            result.setName(dish.getName()); result.setImage(dish.getImage()); result.setAmount(dish.getPrice());
        } else {
            Setmeal meal = setmeals.getForPricing(cart.getSetmealId());
            if (meal == null || !Integer.valueOf(1).equals(meal.getStatus())) {
                throw new OrderBusinessException("套餐不存在或已停售");
            }
            result.setName(meal.getName()); result.setImage(meal.getImage()); result.setAmount(meal.getPrice());
        }
        if (result.getAmount() == null || result.getAmount().signum() <= 0) {
            throw new OrderBusinessException("商品价格不合法");
        }
        return result;
    }

    public Quote quote(List<ShoppingCart> carts, boolean pickup) {
        if (carts == null || carts.isEmpty()) { throw new OrderBusinessException("购物车为空"); }
        List<ShoppingCart> sorted = new ArrayList<>(carts);
        // A stable catalog lock order avoids opposite product lock ordering between users.
        sorted.sort(Comparator.comparing(cart -> cart.getDishId() != null ? "D:" + cart.getDishId() : "S:" + cart.getSetmealId()));
        ArrayList<OrderDetail> details = new ArrayList<>();
        int quantity = 0;
        BigDecimal subtotal = BigDecimal.ZERO;
        for (ShoppingCart cart : sorted) {
            OrderDetail detail = priceItem(cart);
            quantity += detail.getNumber();
            if (quantity > 200) { throw new OrderBusinessException("单笔订单最多预订200份"); }
            subtotal = subtotal.add(detail.getAmount().multiply(BigDecimal.valueOf(detail.getNumber())));
            details.add(detail);
        }
        int packaging = Math.multiplyExact(quantity, packagingPerItem);
        BigDecimal shipping = pickup ? BigDecimal.ZERO.setScale(2) : deliveryFee;
        return new Quote(details, quantity, packaging, shipping,
                subtotal.add(BigDecimal.valueOf(packaging)).add(shipping).setScale(2));
    }

    @Data @AllArgsConstructor
    public static class Quote {
        private ArrayList<OrderDetail> details;
        private int quantity;
        private int packAmount;
        private BigDecimal deliveryFee;
        private BigDecimal amount;
    }
}
