package com.sky.service.impl;

import com.sky.context.BaseContext;
import com.sky.dto.ShoppingCartDTO;
import com.sky.entity.OrderDetail;
import com.sky.entity.ShoppingCart;
import com.sky.exception.OrderBusinessException;
import com.sky.mapper.ShoppingCartMapper;
import com.sky.service.OrderPricingService;
import com.sky.service.ShoppingCartService;
import com.sky.service.UserLockService;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import java.time.Clock;
import java.time.LocalDateTime;
import java.util.List;

@Service
public class ShoppingCartServiceImpl implements ShoppingCartService {
    private final ShoppingCartMapper carts;
    private final OrderPricingService pricing;
    private final UserLockService userLock;
    private final Clock clock;
    public ShoppingCartServiceImpl(ShoppingCartMapper carts,OrderPricingService pricing,UserLockService userLock,Clock clock) {
        this.carts=carts; this.pricing=pricing; this.userLock=userLock; this.clock=clock;
    }
    @Override @Transactional
    public void addShoppingCart(ShoppingCartDTO dto) {
        Long userId=userLock.lockCurrentUser();
        ShoppingCart filter=filter(dto,userId); filter.setNumber(1);
        OrderDetail current=pricing.priceItem(filter);
        List<ShoppingCart> existing=carts.list(filter);
        if (!existing.isEmpty()) {
            ShoppingCart row=existing.get(0);
            if (row.getNumber()>=99) { throw new OrderBusinessException("同一商品最多99份"); }
            row.setNumber(row.getNumber()+1); row.setAmount(current.getAmount()); carts.updateNumberById(row);
        } else {
            filter.setName(current.getName()); filter.setImage(current.getImage()); filter.setAmount(current.getAmount());
            filter.setCreateTime(LocalDateTime.now(clock)); carts.insert(filter);
        }
    }
    @Override public List<ShoppingCart> showShoppingCart() {
        return carts.list(ShoppingCart.builder().userId(BaseContext.getCurrentId()).build());
    }
    @Override @Transactional public void cleanShoppingCart() { carts.deleteByUserId(userLock.lockCurrentUser()); }
    @Override @Transactional
    public void subShoppingCart(ShoppingCartDTO dto) {
        Long userId=userLock.lockCurrentUser();
        List<ShoppingCart> existing=carts.list(filter(dto,userId));
        if (existing.isEmpty()) { return; }
        ShoppingCart row=existing.get(0);
        if (row.getNumber()<=1) { carts.deleteById(row.getId()); }
        else { row.setNumber(row.getNumber()-1); carts.updateNumberById(row); }
    }
    private ShoppingCart filter(ShoppingCartDTO dto,Long userId) {
        if (dto==null || (dto.getDishId()==null)==(dto.getSetmealId()==null)) {
            throw new OrderBusinessException("请选择一个菜品或套餐");
        }
        String flavor=dto.getDishFlavor()==null?"":dto.getDishFlavor();
        if (flavor.length()>50) { throw new OrderBusinessException("口味说明过长"); }
        return ShoppingCart.builder().userId(userId).dishId(dto.getDishId()).setmealId(dto.getSetmealId()).dishFlavor(flavor).build();
    }
}
