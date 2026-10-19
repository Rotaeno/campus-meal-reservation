package com.sky.service;

import com.alibaba.fastjson.JSONObject;
import com.sky.entity.RefundTask;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import java.time.Duration;
import java.time.LocalDateTime;

/** Transport failure is not an external funds result. Bound automatic processing. */
@Component
public class RefundPolicy {
    private final long pollSeconds;
    private final long reviewSeconds;
    public RefundPolicy(@Value("${sky.refund.poll-seconds:60}") long pollSeconds,
                        @Value("${sky.refund.review-after-seconds:86400}") long reviewSeconds) {
        if(pollSeconds<1 || pollSeconds>3600 || reviewSeconds<1 || reviewSeconds>604800) {
            throw new IllegalArgumentException("Invalid refund recovery policy");
        }
        this.pollSeconds=pollSeconds; this.reviewSeconds=reviewSeconds;
    }
    public boolean queryFirst(RefundTask task) {
        return Boolean.TRUE.equals(task.getProviderSubmitted()) || task.getAttempts()>1;
    }
    public String confirmedState(JSONObject response) {
        String state=response==null?null:response.getString("status");
        if("SUCCESS".equals(state) || "LOCAL_SIMULATION".equals(state)) { return "SUCCEEDED"; }
        if("PROCESSING".equals(state) || "CLOSED".equals(state) || "ABNORMAL".equals(state) || "NOT_FOUND".equals(state)) { return state; }
        throw new IllegalStateException("Unconfirmed provider refund response");
    }
    public boolean needsReview(RefundTask task,LocalDateTime now) {
        return age(task,now)>=reviewSeconds;
    }
    public long pollDelay(RefundTask task,LocalDateTime now) {
        long age=age(task,now);
        // Normal default: one minute, then 5/10/20/30 minutes after prolonged processing.
        return age<300 ? pollSeconds : Math.max(pollSeconds, Math.min(1800,300L << Math.min(3,(age-300)/600)));
    }
    private long age(RefundTask task,LocalDateTime now) {
        return Math.max(0,Duration.between(task.getCreateTime(),now).getSeconds());
    }
}
