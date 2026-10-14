package com.sky.controller.user;

import com.sky.context.BaseContext;
import com.sky.entity.Orders;
import com.sky.exception.OrderBusinessException;
import com.sky.mapper.OrderMapper;
import com.sky.result.Result;
import com.sky.service.OrderService;
import com.sky.reliability.LocalProviderClient;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.context.annotation.Profile;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/** Authenticated local simulation; deliberately absent from other profiles. */
@RestController
@Profile("local")
@RequestMapping("/user/local")
public class LocalPaymentController {
    private final OrderMapper orderMapper;
    private final OrderService orderService;
    private final ObjectProvider<LocalProviderClient> provider;

    public LocalPaymentController(OrderMapper orderMapper, OrderService orderService, ObjectProvider<LocalProviderClient> provider) {
        this.orderMapper = orderMapper;
        this.orderService = orderService;
        this.provider = provider;
    }

    @PostMapping("/orders/{number}/pay")
    public Result<String> simulatePayment(@PathVariable String number) {
        Orders order = orderMapper.getByNumberAndUserId(number, BaseContext.getCurrentId());
        if (order == null || !Orders.PENDING_PAYMENT.equals(order.getStatus())
                || !Orders.UN_PAID.equals(order.getPayStatus())) {
            throw new OrderBusinessException("订单不存在或不能进行本地模拟支付");
        }
        recordPayment(number,order.getAmount());
        orderService.paySuccess(number,order.getAmount());
        return Result.success("LOCAL_SIMULATION: no real payment was made");
    }
    @PostMapping("/orders/{number}/callback")
    public Result<String> simulateCallback(@PathVariable String number,@org.springframework.web.bind.annotation.RequestBody java.util.Map<String,java.math.BigDecimal> request) {
        Orders order=orderMapper.getByNumberAndUserId(number,BaseContext.getCurrentId());
        if(order==null) { throw new OrderBusinessException("订单不存在"); }
        java.math.BigDecimal amount=request.get("paidAmount");
        if(amount==null || amount.compareTo(order.getAmount())!=0) { throw new OrderBusinessException("支付金额与订单不一致"); }
        recordPayment(number,amount);
        orderService.paySuccess(number,request.get("paidAmount"));
        return Result.success("LOCAL_SIMULATION: authenticated callback probe");
    }
    private void recordPayment(String number,java.math.BigDecimal amount) {
        LocalProviderClient ledger=provider.getIfAvailable();
        if(ledger!=null) { ledger.paid(number,amount); }
    }
}
