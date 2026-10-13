package com.sky.service;

import com.alibaba.fastjson.JSON;
import com.alibaba.fastjson.JSONObject;
import com.sky.entity.Orders;
import com.sky.entity.RefundTask;
import com.sky.exception.OrderBusinessException;
import com.sky.mapper.OrderMapper;
import com.sky.mapper.RefundTaskMapper;
import com.sky.utils.WeChatPayUtil;
import lombok.extern.slf4j.Slf4j;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionSynchronizationManager;
import org.springframework.transaction.support.TransactionTemplate;
import java.time.Clock;
import java.time.LocalDateTime;
import java.util.List;
import java.util.UUID;

/** A durable refund intent, a reclaimable lease and a stable provider idempotency key. */
@Service @Slf4j
public class RefundService {
    private final RefundTaskMapper tasks;
    private final OrderMapper orders;
    private final WeChatPayUtil gateway;
    private final Clock clock;
    private final TransactionTemplate transaction;
    public RefundService(RefundTaskMapper tasks,OrderMapper orders,WeChatPayUtil gateway,Clock clock,PlatformTransactionManager manager) {
        this.tasks=tasks; this.orders=orders; this.gateway=gateway; this.clock=clock; this.transaction=new TransactionTemplate(manager);
    }
    private LocalDateTime now() { return LocalDateTime.now(clock); }

    public void enqueue(Orders order) {
        if (!TransactionSynchronizationManager.isActualTransactionActive()) { throw new IllegalStateException("Refund intent must join the order transaction"); }
        tasks.enqueue(order.getId(),"R"+order.getNumber(),now());
    }
    public List<RefundTask> list() { return tasks.listAll(); }

    @Scheduled(fixedDelayString="${sky.refund.interval-ms:3000}")
    public void processDue() {
        for(Long id:tasks.due(now())) { process(id); }
    }

    public void process(Long id) {
        String token=UUID.randomUUID().toString().replace("-","");
        LocalDateTime now=now();
        if(tasks.claim(id,token,now.plusMinutes(2),now)!=1) { return; }
        RefundTask task=tasks.getById(id);
        try {
            Orders order=orders.getById(task.getOrderId());
            if (order==null || !Orders.CANCELLED.equals(order.getStatus()) || !Orders.PAID.equals(order.getPayStatus())) {
                throw new IllegalStateException("Refund order is not cancelled and paid");
            }
            // Provider calls happen outside a DB transaction and always reuse the same refund number.
            String body=Boolean.TRUE.equals(task.getProviderSubmitted()) ? gateway.queryRefund(task.getRefundNumber())
                    : gateway.refund(order.getNumber(),task.getRefundNumber(),order.getAmount(),order.getAmount());
            JSONObject response=JSON.parseObject(body);
            String status=response==null?null:response.getString("status");
            if("SUCCESS".equals(status) || "LOCAL_SIMULATION".equals(status)) {
                transaction.execute(result -> {
                    orders.getByIdForUpdate(task.getOrderId()); // Consistent order -> refund lock order.
                    if(tasks.succeed(id,token,now())==1 && orders.markRefunded(task.getOrderId())!=1) {
                        throw new IllegalStateException("Refund completion conflicts with order state");
                    }
                    return null;
                });
            } else if("PROCESSING".equals(status)) {
                tasks.reschedule(id,token,"PENDING",true,task.getFailures(),now().plusSeconds(30),null,now());
            } else { throw new IllegalStateException("Provider refund response did not confirm success"); }
        } catch(Exception exception) {
            int failures=task.getFailures()+1;
            String status=failures>=6?"FAILED":"PENDING";
            // Store a bounded diagnostic, never merchant keys or complete HTTP payloads.
            String error=exception.getClass().getSimpleName();
            transaction.execute(result -> {
                orders.getByIdForUpdate(task.getOrderId());
                int changed=tasks.reschedule(id,token,status,task.getProviderSubmitted(),failures,
                        now().plusSeconds(Math.min(60,1L<<Math.min(failures,6))),error,now());
                if(changed==1 && "FAILED".equals(status)) { orders.markRefundFailed(task.getOrderId()); }
                return null;
            });
            log.warn("Refund task {} will be retried or reviewed, attempt {}",id,failures);
        }
    }

    public void retry(Long id) {
        transaction.execute(result -> {
            RefundTask task=tasks.getById(id);
            if(task==null) { throw new OrderBusinessException("退款任务不存在"); }
            orders.getByIdForUpdate(task.getOrderId());
            if(tasks.retry(id,now())!=1) { throw new OrderBusinessException("仅失败退款任务可人工重试"); }
            orders.markRefundPending(task.getOrderId()); return null;
        });
    }
}
