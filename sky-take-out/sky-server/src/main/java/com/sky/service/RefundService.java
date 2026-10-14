package com.sky.service;

import com.alibaba.fastjson.JSON;
import com.alibaba.fastjson.JSONObject;
import com.sky.entity.Orders;
import com.sky.entity.RefundTask;
import com.sky.exception.OrderBusinessException;
import com.sky.mapper.OrderMapper;
import com.sky.mapper.RefundTaskMapper;
import com.sky.utils.WeChatPayUtil;
import com.sky.reliability.DatabaseRetry;
import com.sky.reliability.ReliabilityProbe;
import org.springframework.beans.factory.annotation.Value;
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
    private final DatabaseRetry retry;
    private final ReliabilityProbe reliability;
    private final long leaseSeconds;
    private final boolean scanEnabled;
    public RefundService(RefundTaskMapper tasks,OrderMapper orders,WeChatPayUtil gateway,Clock clock,PlatformTransactionManager manager,
                         DatabaseRetry retry,ReliabilityProbe reliability,
                         @Value("${sky.refund.lease-seconds:120}") long leaseSeconds,
                         @Value("${sky.refund.scan-enabled:true}") boolean scanEnabled) {
        this.tasks=tasks; this.orders=orders; this.gateway=gateway; this.clock=clock; this.transaction=new TransactionTemplate(manager);
        this.retry=retry; this.reliability=reliability; this.leaseSeconds=leaseSeconds; this.scanEnabled=scanEnabled;
        if(leaseSeconds<1 || leaseSeconds>600) { throw new IllegalArgumentException("Invalid refund lease duration"); }
    }
    private LocalDateTime now() { return LocalDateTime.now(clock); }

    public void enqueue(Orders order) {
        if (!TransactionSynchronizationManager.isActualTransactionActive()) { throw new IllegalStateException("Refund intent must join the order transaction"); }
        tasks.enqueue(order.getId(),"R"+order.getNumber(),now());
    }
    public List<RefundTask> list() { return tasks.listAll(); }

    @Scheduled(fixedDelayString="${sky.refund.interval-ms:3000}")
    public void processDue() {
        if(!scanEnabled) { return; }
        processAvailable();
    }
    public void processAvailable() {
        for(Long id:tasks.due(now())) { process(id); }
    }

    public void process(Long id) {
        if(TransactionSynchronizationManager.isActualTransactionActive()) { throw new IllegalStateException("Provider work cannot join a DB transaction"); }
        String token=UUID.randomUUID().toString().replace("-","");
        RefundTask task=retry.transaction(transaction,result -> {
            LocalDateTime claimedAt=now();
            return tasks.claim(id,token,claimedAt.plusSeconds(leaseSeconds),claimedAt)==1 ? tasks.getById(id) : null;
        });
        if(task==null) { return; }
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
                reliability.hit("refund.providerConfirmed",task.getRefundNumber());
                retry.transaction(transaction,result -> {
                    orders.getByIdForUpdate(task.getOrderId()); // Consistent order -> refund lock order.
                    if(tasks.succeed(id,token,now())==1 && orders.markRefunded(task.getOrderId())!=1) {
                        throw new IllegalStateException("Refund completion conflicts with order state");
                    }
                    reliability.refundCommit(task.getRefundNumber());
                    return null;
                });
            } else if("PROCESSING".equals(status)) {
                retry.transaction(transaction,result -> tasks.reschedule(id,token,"PENDING",true,task.getFailures(),now().plusSeconds(30),null,now()));
            } else { throw new IllegalStateException("Provider refund response did not confirm success"); }
        } catch(Exception exception) {
            int failures=task.getFailures()+1;
            String status=failures>=6?"FAILED":"PENDING";
            // Store a bounded diagnostic, never merchant keys or complete HTTP payloads.
            String error=exception.getClass().getSimpleName();
            retry.transaction(transaction,result -> {
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
        retry.transaction(transaction,result -> {
            RefundTask task=tasks.getById(id);
            if(task==null) { throw new OrderBusinessException("退款任务不存在"); }
            orders.getByIdForUpdate(task.getOrderId());
            if(tasks.retry(id,now())!=1) { throw new OrderBusinessException("仅失败退款任务可人工重试"); }
            orders.markRefundPending(task.getOrderId()); return null;
        });
    }
}
