package com.sky.service.impl;

import com.alibaba.fastjson.JSON;
import com.alibaba.fastjson.JSONObject;
import com.github.pagehelper.Page;
import com.github.pagehelper.PageHelper;
import com.sky.context.BaseContext;
import com.sky.dto.*;
import com.sky.entity.*;
import com.sky.exception.OrderBusinessException;
import com.sky.mapper.*;
import com.sky.result.PageResult;
import com.sky.service.*;
import com.sky.utils.WeChatPayUtil;
import com.sky.vo.*;
import com.sky.websocket.WebSocketServer;
import com.sky.reliability.RetryDatabaseTransaction;
import com.sky.reliability.ReliabilityProbe;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.BeanUtils;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.data.redis.core.RedisTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.time.Clock;
import java.time.LocalDateTime;
import java.util.*;
import java.util.stream.Collectors;

@Service @Slf4j
public class OrderServiceImpl implements OrderService {
    @Autowired private OrderMapper orderMapper;
    @Autowired private OrderDetailMapper orderDetailMapper;
    @Autowired private ShoppingCartMapper shoppingCartMapper;
    @Autowired private AddressBookMapper addressBookMapper;
    @Autowired private MealSessionMapper mealSessionMapper;
    @Autowired private UserMapper userMapper;
    @Autowired private UserLockService userLock;
    @Autowired private OrderPricingService pricing;
    @Autowired private ShoppingCartService cartService;
    @Autowired private RefundService refunds;
    @Autowired private DeliveryRangeService deliveryRange;
    @Autowired private WeChatPayUtil weChatPayUtil;
    @Autowired private WebSocketServer webSocketServer;
    @Autowired private RedisTemplate redisTemplate;
    @Autowired private Clock clock;
    @Autowired private ReliabilityProbe reliability;
    private static final SecureRandom RANDOM=new SecureRandom();
    private LocalDateTime now() { return LocalDateTime.now(clock); }

    // Missing idempotency keys must not retain RR gap locks that block other users' inserts.
    // User/catalog row locks and the atomic capacity UPDATE still protect all mutable inputs.
    @Override @RetryDatabaseTransaction @Transactional(rollbackFor=Exception.class,isolation=Isolation.READ_COMMITTED)
    public OrderSubmitVO submitOrder(OrdersSubmitDTO request) {
        validateRequest(request);
        Long userId=userLock.lockCurrentUser();
        String fingerprint=fingerprint(request);
        // Current locking read: a transaction waiting on the user row must see the preceding commit.
        Orders existing=orderMapper.getByRequest(userId,request.getRequestKey());
        if (existing!=null) {
            if (!fingerprint.equals(existing.getRequestHash())) { throw new OrderBusinessException("该请求键已用于其他订单，请创建新的请求键"); }
            return submitted(existing);
        }
        if (!Integer.valueOf(1).equals(redisTemplate.opsForValue().get("SHOP_STATUS"))) { throw new OrderBusinessException("店铺已打烊"); }
        boolean pickup=request.getMealSessionId()!=null;
        AddressBook address=null;
        if (!pickup) {
            address=addressBookMapper.getOwned(request.getAddressBookId(),userId);
            if (address==null) { throw new OrderBusinessException("地址不存在"); }
            deliveryRange.check(address.getCityName()+address.getDistrictName()+address.getDetail());
        }
        OrderPricingService.Quote quote=pricing.quote(shoppingCartMapper.listForUpdate(userId),pickup);
        LocalDateTime now=now();
        MealSession session=null;
        if (pickup) {
            if (mealSessionMapper.reserve(request.getMealSessionId(),quote.getQuantity(),now)!=1) {
                throw new OrderBusinessException("餐次已截单、关闭或剩余名额不足");
            }
            session=mealSessionMapper.getById(request.getMealSessionId());
        }
        Orders order=new Orders();
        order.setNumber(UUID.randomUUID().toString().replace("-",""));
        order.setUserId(userId); order.setRequestKey(request.getRequestKey()); order.setRequestHash(fingerprint);
        order.setStatus(Orders.PENDING_PAYMENT); order.setPayStatus(Orders.UN_PAID); order.setOrderTime(now);
        order.setAmount(quote.getAmount()); order.setPackAmount(quote.getPackAmount()); order.setDeliveryFee(quote.getDeliveryFee());
        order.setPayMethod(1); order.setRemark(request.getRemark()); order.setTablewareNumber(request.getTablewareNumber()==null?quote.getQuantity():request.getTablewareNumber());
        order.setTablewareStatus(request.getTablewareStatus()==null?1:request.getTablewareStatus()); order.setDeliveryStatus(pickup?0:(request.getDeliveryStatus()==null?1:request.getDeliveryStatus()));
        order.setFulfillmentType(pickup?2:1); order.setMealSessionId(request.getMealSessionId());
        order.setReservedQuantity(pickup?quote.getQuantity():0); order.setRefundStatus("NONE");
        if (pickup) {
            byte[] bytes=new byte[16]; RANDOM.nextBytes(bytes);
            order.setPickupCode(Base64.getUrlEncoder().withoutPadding().encodeToString(bytes));
            order.setConsignee(request.getContactName().trim()); order.setPhone(request.getContactPhone());
            order.setAddress("校园自提 · "+session.getTitle()); order.setEstimatedDeliveryTime(session.getPickupStart());
        } else {
            order.setAddressBookId(address.getId()); order.setConsignee(address.getConsignee()); order.setPhone(address.getPhone());
            order.setAddress(address.getProvinceName()+address.getCityName()+address.getDistrictName()+address.getDetail());
            order.setEstimatedDeliveryTime(request.getEstimatedDeliveryTime()==null?now.plusMinutes(30):request.getEstimatedDeliveryTime());
        }
        orderMapper.insert(order);
        quote.getDetails().forEach(detail -> detail.setOrderId(order.getId()));
        orderDetailMapper.insertBatch(quote.getDetails());
        shoppingCartMapper.deleteByUserId(userId);
        reliability.submission(request.getRequestKey());
        return submitted(order);
    }

    @Override @Transactional(isolation=Isolation.READ_COMMITTED)
    public OrderPricingService.Quote quote(Long sessionId) {
        Long userId=userLock.lockCurrentUser();
        return pricing.quote(shoppingCartMapper.listForUpdate(userId),sessionId!=null);
    }

    private void validateRequest(OrdersSubmitDTO request) {
        if (request==null || request.getRequestKey()==null || !request.getRequestKey().matches("[A-Za-z0-9_-]{8,64}")) {
            throw new OrderBusinessException("请提供8至64位的稳定请求键 requestKey");
        }
        if (request.getRemark()!=null && request.getRemark().length()>100) { throw new OrderBusinessException("备注最多100字"); }
        if (request.getTablewareNumber()!=null && (request.getTablewareNumber()<0 || request.getTablewareNumber()>200)) {
            throw new OrderBusinessException("餐具数量不合法");
        }
        if ((request.getTablewareStatus()!=null && request.getTablewareStatus()!=0 && request.getTablewareStatus()!=1)
                || (request.getDeliveryStatus()!=null && request.getDeliveryStatus()!=0 && request.getDeliveryStatus()!=1)) {
            throw new OrderBusinessException("餐具或配送状态不合法");
        }
        if (request.getMealSessionId()!=null && (request.getContactName()==null || request.getContactName().trim().isEmpty()
                || request.getContactName().length()>32 || request.getContactPhone()==null || !request.getContactPhone().matches("[0-9]{11}"))) {
            throw new OrderBusinessException("自提订单需填写取餐人及11位手机号");
        }
        if (request.getMealSessionId()==null && request.getAddressBookId()==null) { throw new OrderBusinessException("请选择收货地址"); }
    }

    private String fingerprint(OrdersSubmitDTO request) {
        try {
            String semantic=JSON.toJSONString(Arrays.asList(request.getAddressBookId(),request.getMealSessionId(),request.getContactName(),
                    request.getContactPhone(),request.getRemark(),request.getEstimatedDeliveryTime(),request.getDeliveryStatus(),request.getTablewareNumber(),request.getTablewareStatus()));
            byte[] digest=MessageDigest.getInstance("SHA-256").digest(semantic.getBytes(StandardCharsets.UTF_8));
            StringBuilder result=new StringBuilder(); for(byte value:digest) { result.append(String.format("%02x",value&255)); }
            return result.toString();
        } catch (Exception exception) { throw new IllegalStateException("Request fingerprint failed",exception); }
    }

    private OrderSubmitVO submitted(Orders order) {
        return OrderSubmitVO.builder().id(order.getId()).orderNumber(order.getNumber()).orderAmount(order.getAmount()).orderTime(order.getOrderTime())
                .packAmount(order.getPackAmount()).deliveryFee(order.getDeliveryFee()).fulfillmentType(order.getFulfillmentType())
                .mealSessionId(order.getMealSessionId()).pickupCode(order.getPickupCode()).build();
    }

    @Override public OrderPaymentVO payment(OrdersPaymentDTO request) throws Exception {
        Orders order=orderMapper.getByNumberAndUserId(request.getOrderNumber(),BaseContext.getCurrentId());
        if (order==null || !Orders.PENDING_PAYMENT.equals(order.getStatus()) || !Orders.UN_PAID.equals(order.getPayStatus()) || expired(order,now())) {
            throw new OrderBusinessException("订单不存在、已支付或支付时间已过");
        }
        User user=userMapper.getById(String.valueOf(BaseContext.getCurrentId()));
        if (user==null) { throw new OrderBusinessException("请先登录"); }
        JSONObject response=weChatPayUtil.pay(order.getNumber(),order.getAmount(),"校园午餐订单",user.getOpenid());
        if (response.getString("code")!=null) { throw new OrderBusinessException("预支付创建失败"); }
        OrderPaymentVO result=response.toJavaObject(OrderPaymentVO.class); result.setPackageStr(response.getString("package")); return result;
    }

    @Override @RetryDatabaseTransaction @Transactional
    public void paySuccess(String number,BigDecimal paidAmount) {
        Orders order=orderMapper.getByNumberForUpdate(number);
        if (order==null) { throw new OrderBusinessException("订单不存在"); }
        if (paidAmount==null || order.getAmount().compareTo(paidAmount)!=0) { throw new OrderBusinessException("支付金额与订单不一致"); }
        if (!Orders.UN_PAID.equals(order.getPayStatus())) { return; }
        LocalDateTime now=now();
        if (Orders.CANCELLED.equals(order.getStatus()) || expired(order,now)) {
            // Updating the child status can take a shared FK lock on the meal row.
            // Take the parent X lock first, avoiding concurrent S -> X upgrades on release.
            if (!Orders.CANCELLED.equals(order.getStatus())) { releaseReservation(order); }
            if (orderMapper.markLatePaid(order.getId(),now)!=1) { throw new OrderBusinessException("订单状态已改变"); }
            refunds.enqueue(order);
            return;
        }
        if (orderMapper.markPaid(order.getId(),now)!=1) { throw new OrderBusinessException("订单状态已改变"); }
        notifyAfterCommit(1,order);
    }

    private boolean expired(Orders order,LocalDateTime now) {
        if (order.getOrderTime()!=null && !order.getOrderTime().plusMinutes(15).isAfter(now)) { return true; }
        if (order.getMealSessionId()!=null) {
            MealSession session=mealSessionMapper.getById(order.getMealSessionId());
            return session==null || !session.getCutoffTime().isAfter(now);
        }
        return false;
    }

    @Override @RetryDatabaseTransaction @Transactional public boolean expireOrder(Long id) {
        Orders order=orderMapper.getByIdForUpdate(id);
        if (order==null || !Orders.PENDING_PAYMENT.equals(order.getStatus()) || !Orders.UN_PAID.equals(order.getPayStatus()) || !expired(order,now())) { return false; }
        cancelLocked(order,"支付超时或餐次截单，自动取消"); return true;
    }

    @Override @RetryDatabaseTransaction @Transactional public void userCancelById(Long id) {
        Orders order=ownedLocked(id);
        if (Orders.CANCELLED.equals(order.getStatus())) { return; }
        if (!Orders.PENDING_PAYMENT.equals(order.getStatus()) && !Orders.TO_BE_CONFIRMED.equals(order.getStatus())) { throw new OrderBusinessException("当前订单状态不能取消"); }
        if (order.getMealSessionId()!=null) {
            MealSession session=mealSessionMapper.getById(order.getMealSessionId());
            if (session==null || !session.getCutoffTime().isAfter(now())) { throw new OrderBusinessException("餐次已截单，请联系商家处理"); }
        }
        cancelLocked(order,"用户取消");
    }

    private void cancelLocked(Orders order,String reason) {
        // Order is locked. Release first in the SAME transaction: later SQL failure rolls it back.
        // status is part of the meal FK-supporting secondary index; its update can acquire parent S.
        releaseReservation(order);
        if (orderMapper.cancelIfMatches(order.getId(),order.getStatus(),order.getPayStatus(),reason,now())!=1) { throw new OrderBusinessException("订单状态已改变"); }
        if (Orders.PAID.equals(order.getPayStatus())) { refunds.enqueue(order); }
    }
    private void releaseReservation(Orders order) {
        if (order.getMealSessionId()!=null && order.getReservedQuantity()!=null && order.getReservedQuantity()>0
                && mealSessionMapper.release(order.getMealSessionId(),order.getReservedQuantity())!=1) {
            throw new IllegalStateException("Reservation counter is inconsistent");
        }
    }
    private Orders ownedLocked(Long id) {
        Orders order=orderMapper.getByIdForUpdate(id);
        if (order==null || !Objects.equals(order.getUserId(),BaseContext.getCurrentId())) { throw new OrderBusinessException("订单不存在"); }
        return order;
    }
    private Orders owned(Long id) {
        Orders order=orderMapper.getOwned(id,BaseContext.getCurrentId());
        if (order==null) { throw new OrderBusinessException("订单不存在"); }
        return order;
    }

    @Override @Transactional public void repetition(Long id) {
        userLock.lockCurrentUser(); owned(id);
        List<OrderDetail> details=orderDetailMapper.getByOrderId(id);
        for (OrderDetail detail:details) {
            ShoppingCartDTO dto=new ShoppingCartDTO(); dto.setDishId(detail.getDishId()); dto.setSetmealId(detail.getSetmealId()); dto.setDishFlavor(detail.getDishFlavor());
            for(int n=0;n<detail.getNumber();n++) { cartService.addShoppingCart(dto); }
        }
    }
    @Override public OrderVO details(Long id) {
        Orders order=orderMapper.getById(id); if(order==null) { throw new OrderBusinessException("订单不存在"); }
        return view(order,orderDetailMapper.getByOrderId(id));
    }
    @Override public OrderVO userDetails(Long id) { Orders order=owned(id); return view(order,orderDetailMapper.getByOrderId(id)); }
    @Override public void reminder(Long id) {
        Orders order=owned(id);
        if (!Arrays.asList(2,3,4).contains(order.getStatus())) { throw new OrderBusinessException("当前订单不能催单"); }
        notifyAfterCommit(2,order);
    }
    private void notifyAfterCommit(int type,Orders order) {
        Map<String,Object> payload=new HashMap<>(); payload.put("type",type); payload.put("orderId",order.getId()); payload.put("content","订单号："+order.getNumber());
        Runnable send=()->webSocketServer.sendToAllClient(JSON.toJSONString(payload));
        if (TransactionSynchronizationManager.isSynchronizationActive()) {
            TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
                @Override public void afterCommit() { send.run(); }
            });
        } else { send.run(); }
    }
    @Override public void confirm(OrdersCancelDTO request) { transition(request.getId(),2,3,false); }
    @Override public void delivery(Long id) { transition(id,3,4,true); }
    @Override public void complete(Long id) { transition(id,4,5,true); }
    private void transition(Long id,int expected,int next,boolean deliveryOnly) {
        Orders order=orderMapper.getById(id);
        if (order==null || (deliveryOnly && Integer.valueOf(2).equals(order.getFulfillmentType()))
                || orderMapper.transition(id,expected,next,now())!=1) { throw new OrderBusinessException("订单状态错误，自提订单须通过备餐和核销流程完成"); }
    }
    @Override @RetryDatabaseTransaction @Transactional public void rejection(OrdersRejectionDTO request) {
        Orders order=orderMapper.getByIdForUpdate(request.getId());
        if(order==null || !Orders.TO_BE_CONFIRMED.equals(order.getStatus())) { throw new OrderBusinessException("订单状态错误"); }
        cancelLocked(order,"商家拒单："+safeReason(request.getRejectionReason()));
    }
    @Override @RetryDatabaseTransaction @Transactional public void cancel(OrdersCancelDTO request) {
        Orders order=orderMapper.getByIdForUpdate(request.getId());
        if(order==null) { throw new OrderBusinessException("订单不存在"); }
        if(Orders.CANCELLED.equals(order.getStatus())) { return; }
        if(Orders.COMPLETED.equals(order.getStatus())) { throw new OrderBusinessException("已完成订单不能取消"); }
        cancelLocked(order,"商家取消："+safeReason(request.getCancelReason()));
    }
    private String safeReason(String reason) { return reason==null?"未填写":reason.substring(0,Math.min(reason.length(),80)); }

    @Override public PageResult pageQueryForUser(int pageNum,int pageSize,Integer status) {
        OrdersPageQueryDTO query=new OrdersPageQueryDTO(); query.setUserId(BaseContext.getCurrentId()); query.setStatus(status); query.setPage(pageNum); query.setPageSize(pageSize);
        return page(query);
    }
    @Override public PageResult conditionSearch(OrdersPageQueryDTO query) { return page(query); }
    private PageResult page(OrdersPageQueryDTO query) {
        if(query.getPage()<1 || query.getPageSize()<1 || query.getPageSize()>100) { throw new OrderBusinessException("分页参数不合法"); }
        PageHelper.startPage(query.getPage(),query.getPageSize()); Page<Orders> page=orderMapper.pageQuery(query);
        List<OrderVO> views=new ArrayList<>();
        if(!page.isEmpty()) {
            Map<Long,List<OrderDetail>> details=orderDetailMapper.getByOrderIds(page.stream().map(Orders::getId).collect(Collectors.toList()))
                    .stream().collect(Collectors.groupingBy(OrderDetail::getOrderId));
            page.forEach(order->views.add(view(order,details.getOrDefault(order.getId(),Collections.emptyList()))));
        }
        return new PageResult(page.getTotal(),views);
    }
    private OrderVO view(Orders order,List<OrderDetail> details) {
        OrderVO result=new OrderVO(); BeanUtils.copyProperties(order,result); result.setOrderDetailList(details);
        result.setOrderDishes(details.stream().map(detail->detail.getName()+"*"+detail.getNumber()).collect(Collectors.joining(";")));
        return result;
    }
    @Override public OrderStatisticsVO statistics() {
        OrderStatisticsVO result=new OrderStatisticsVO(); result.setToBeConfirmed(orderMapper.countStatus(2)); result.setConfirmed(orderMapper.countStatus(3)); result.setDeliveryInProgress(orderMapper.countStatus(4)); return result;
    }
}
