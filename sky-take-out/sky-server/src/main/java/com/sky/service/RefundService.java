package com.sky.service;

import com.alibaba.fastjson.JSON;
import com.alibaba.fastjson.JSONObject;
import com.sky.entity.Orders;
import com.sky.entity.RefundTask;
import com.sky.entity.RefundReview;
import com.sky.context.BaseContext;
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
import java.util.TreeSet;
import java.util.stream.Collectors;
import java.util.UUID;

/** A durable refund intent, a reclaimable lease and a stable provider idempotency key. */
@Service @Slf4j
public class RefundService {
    private final RefundTaskMapper tasks;
    private final OrderMapper orders;
    private final WeChatPayUtil gateway;
    private final Clock clock;
    private final TransactionTemplate transaction;
    private final TransactionTemplate candidateTransaction;
    private final DatabaseRetry retry;
    private final ReliabilityProbe reliability;
    private final long leaseSeconds;
    private final boolean scanEnabled;
    private final RefundDispatcher dispatcher;
    private final RefundPolicy policy;
    public RefundService(RefundTaskMapper tasks,OrderMapper orders,WeChatPayUtil gateway,Clock clock,PlatformTransactionManager manager,
                         DatabaseRetry retry,ReliabilityProbe reliability,
                         @Value("${sky.refund.lease-seconds:120}") long leaseSeconds,
                         @Value("${sky.refund.scan-enabled:true}") boolean scanEnabled,
                         RefundDispatcher dispatcher,RefundPolicy policy) {
        this.tasks=tasks; this.orders=orders; this.gateway=gateway; this.clock=clock; this.transaction=new TransactionTemplate(manager);
        this.retry=retry; this.reliability=reliability; this.leaseSeconds=leaseSeconds; this.scanEnabled=scanEnabled;
        this.candidateTransaction=new TransactionTemplate(manager);
        this.candidateTransaction.setIsolationLevel(org.springframework.transaction.TransactionDefinition.ISOLATION_READ_COMMITTED);
        this.dispatcher=dispatcher;
        this.policy=policy;
        if(leaseSeconds<1 || leaseSeconds>600) { throw new IllegalArgumentException("Invalid refund lease duration"); }
    }
    private LocalDateTime now() { return LocalDateTime.now(clock); }

    public void enqueue(Orders order) {
        if (!TransactionSynchronizationManager.isActualTransactionActive()) { throw new IllegalStateException("Refund intent must join the order transaction"); }
        tasks.enqueue(order.getId(),"R"+order.getNumber(),now());
    }
    public List<RefundTask> list() { return tasks.listAll(); }
    public List<RefundReview> reviews(Long id) { return tasks.reviews(id); }

    @Scheduled(fixedDelayString="${sky.refund.interval-ms:3000}")
    public void processDue() {
        if(!scanEnabled) { return; }
        // The scheduler never waits for the provider. Claim/lease starts only inside an executing worker.
        List<Long> candidates=retry.transaction(candidateTransaction,result -> {
            LocalDateTime at=now();
            TreeSet<Long> ids=new TreeSet<>(tasks.pendingCandidates(at));
            ids.addAll(tasks.expiredLeaseCandidates(at));
            return ids.stream().limit(20).collect(Collectors.toList());
        }); // Release candidate row locks BEFORE queue admission; no claim/lease here.
        for(Long id:candidates) {
            if(reliability.synchronousRefundScan()) { process(id); } // Opt-in local ablation, default off.
            else { dispatcher.dispatch(id, () -> process(id)); }
        }
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
            String body=policy.queryFirst(task) ? gateway.queryRefund(task.getRefundNumber())
                    : gateway.refund(order.getNumber(),task.getRefundNumber(),order.getAmount(),order.getAmount());
            String status=policy.confirmedState(JSON.parseObject(body));
            if("NOT_FOUND".equals(status)) {
                // Absence is not proof that a prior call never reached the provider.
                // Re-submit only the SAME immutable business number and amount.
                status=policy.confirmedState(JSON.parseObject(gateway.refund(order.getNumber(),task.getRefundNumber(),order.getAmount(),order.getAmount())));
            }
            task.setProviderState(status);
            if(!"NOT_FOUND".equals(status)) { task.setProviderSubmitted(true); }
            if("SUCCEEDED".equals(status)) {
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
                boolean review=policy.needsReview(task,now());
                reschedule(task,token,review?"FAILED":"PENDING",true,task.getFailures(),
                        policy.pollDelay(task,now()),review?"PROCESSING_TOO_LONG":null,"PROCESSING");
            } else if("CLOSED".equals(status) || "ABNORMAL".equals(status)) {
                reschedule(task,token,"FAILED",true,task.getFailures(),0,"PROVIDER_"+status,status);
            } else { throw new IllegalStateException("Provider refund response did not confirm success"); }
        } catch(Exception exception) {
            int failures=task.getFailures()+1;
            String status=failures>=6?"FAILED":"PENDING";
            // Store a bounded diagnostic, never merchant keys or complete HTTP payloads.
            String error=exception.getClass().getSimpleName();
            reschedule(task,token,status,task.getProviderSubmitted(),failures,
                    Math.min(60,1L<<Math.min(failures,6)),error,task.getProviderState());
            log.warn("Refund task {} will be retried or reviewed, attempt {}",id,failures);
        }
    }

    private void reschedule(RefundTask task,String token,String status,Boolean submitted,int failures,
                            long delay,String error,String providerState) {
        retry.transaction(transaction,result -> {
            orders.getByIdForUpdate(task.getOrderId());
            int changed=tasks.reschedule(task.getId(),token,status,submitted,failures,now().plusSeconds(delay),
                    error,providerState,now());
            if(changed==1 && "FAILED".equals(status)) { orders.markRefundFailed(task.getOrderId()); }
            return null;
        });
    }

    public void retry(Long id,String reason) {
        if(reason==null || reason.trim().isEmpty() || reason.length()>200 || BaseContext.getCurrentId()==null) {
            throw new OrderBusinessException("请填写200字以内的人工重试原因");
        }
        retry.transaction(transaction,result -> {
            RefundTask task=tasks.getById(id);
            if(task==null) { throw new OrderBusinessException("退款任务不存在"); }
            Orders order=orders.getByIdForUpdate(task.getOrderId());
            if(order==null || !Orders.CANCELLED.equals(order.getStatus()) || !Orders.PAID.equals(order.getPayStatus())) {
                throw new OrderBusinessException("订单状态不允许重试退款，请核实处理结果");
            }
            RefundTask current=tasks.getByIdForUpdate(id);
            if(tasks.retry(id,now())!=1) { throw new OrderBusinessException("仅失败退款任务可人工重试"); }
            RefundReview review=new RefundReview(); review.setTaskId(id); review.setOperatorId(BaseContext.getCurrentId());
            review.setReason(reason.trim()); review.setPreviousProviderState(current.getProviderState());
            review.setPreviousFailures(current.getFailures()); review.setCreateTime(now()); tasks.recordReview(review);
            orders.markRefundPending(task.getOrderId()); return null;
        });
    }
}
