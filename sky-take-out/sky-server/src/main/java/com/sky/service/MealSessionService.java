package com.sky.service;

import com.sky.entity.MealSession;
import com.sky.entity.Orders;
import com.sky.exception.OrderBusinessException;
import com.sky.mapper.MealSessionMapper;
import com.sky.mapper.OrderMapper;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Clock;
import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;

@Service
public class MealSessionService {
    private final MealSessionMapper sessions;
    private final OrderMapper orders;
    private final Clock clock;
    public MealSessionService(MealSessionMapper sessions, OrderMapper orders, Clock clock) {
        this.sessions = sessions; this.orders = orders; this.clock = clock;
    }
    public List<MealSession> listAll() { return sessions.listAll(); }
    public List<MealSession> listOpen() { return sessions.listOpen(LocalDateTime.now(clock)); }
    public LocalDateTime now() { return LocalDateTime.now(clock); }

    @Transactional
    public MealSession create(MealSession session) {
        LocalDateTime now = now();
        if (session.getTitle() == null || session.getTitle().trim().isEmpty() || session.getTitle().length() > 64
                || session.getMealDate() == null || session.getCutoffTime() == null
                || session.getPickupStart() == null || session.getPickupEnd() == null
                || session.getCapacity() == null || session.getCapacity() < 1 || session.getCapacity() > 5000
                || !session.getCutoffTime().isAfter(now)
                || !session.getPickupStart().isAfter(session.getCutoffTime())
                || !session.getPickupEnd().isAfter(session.getPickupStart())
                || !session.getMealDate().equals(session.getPickupStart().toLocalDate())
                || !session.getMealDate().equals(session.getPickupEnd().toLocalDate())
                || session.getMealDate().isAfter(now.toLocalDate().plusDays(7))) {
            throw new OrderBusinessException("餐次需在未来7天内，且截单时间早于自提开始、自提结束晚于开始");
        }
        session.setId(null); session.setTitle(session.getTitle().trim());
        session.setReservedCount(0); session.setStatus(1); session.setCreateTime(now);
        sessions.insert(session);
        return session;
    }

    public void setStatus(Long id, Integer status) {
        if (status == null || (status != 0 && status != 1) || sessions.setStatus(id,status) != 1) {
            throw new OrderBusinessException("餐次或状态不合法");
        }
    }

    @Transactional
    public void ready(Long id) {
        Orders order = orders.getById(id);
        if (order == null || !Integer.valueOf(2).equals(order.getFulfillmentType())
                || orders.transition(id, Orders.CONFIRMED, Orders.DELIVERY_IN_PROGRESS, now()) != 1) {
            throw new OrderBusinessException("仅已接单且已支付的自提订单可标记备餐就绪");
        }
    }

    @Transactional
    public Orders redeem(String code) {
        if (code == null || !code.matches("[A-Za-z0-9_-]{22}")) { throw new OrderBusinessException("核销码不合法"); }
        Orders order = orders.getByPickupCodeForUpdate(code);
        if (order == null || !Integer.valueOf(2).equals(order.getFulfillmentType())) {
            throw new OrderBusinessException("核销码不合法");
        }
        MealSession session = sessions.getById(order.getMealSessionId());
        LocalDateTime now = now();
        if (session == null || now.isBefore(session.getPickupStart()) || now.isAfter(session.getPickupEnd())) {
            throw new OrderBusinessException("当前不在该餐次自提时段内");
        }
        if (orders.transition(order.getId(),Orders.DELIVERY_IN_PROGRESS,Orders.COMPLETED,now) != 1) {
            throw new OrderBusinessException("订单未备餐就绪或已经核销");
        }
        return orders.getById(order.getId());
    }

    public List<Orders> orders(Long id) { return orders.getBySession(id); }
    public List<Map<String,Object>> preparation(Long id) { return sessions.preparationList(id); }
}
