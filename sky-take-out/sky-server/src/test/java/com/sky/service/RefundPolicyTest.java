package com.sky.service;

import com.alibaba.fastjson.JSON;
import com.sky.entity.RefundTask;
import com.sky.utils.WeChatPayUtil;
import org.junit.jupiter.api.Test;
import org.springframework.test.util.ReflectionTestUtils;
import java.time.LocalDateTime;
import static org.junit.jupiter.api.Assertions.*;

class RefundPolicyTest {
    private final LocalDateTime now=LocalDateTime.of(2026,10,19,12,0);
    private RefundTask task(int attempts,long age) {
        RefundTask task=new RefundTask(); task.setAttempts(attempts); task.setProviderSubmitted(false);
        task.setCreateTime(now.minusSeconds(age)); return task;
    }
    @Test void aReclaimedAttemptQueriesEvenIfTheFirstExecutorNeverStoredAProviderResponse() {
        RefundPolicy policy=new RefundPolicy(60,86400);
        assertFalse(policy.queryFirst(task(1,0)));
        assertTrue(policy.queryFirst(task(2,0)));
    }
    @Test void unknownOrErrorResponsesCannotBeTreatedAsSuccessOrAbsence() {
        RefundPolicy policy=new RefundPolicy(60,86400);
        assertThrows(IllegalStateException.class,()->policy.confirmedState(JSON.parseObject("{\"code\":\"SYSTEM_ERROR\"}")));
        assertThrows(IllegalStateException.class,()->policy.confirmedState(JSON.parseObject("{\"status\":\"FAILED\"}")));
        assertThrows(IllegalStateException.class,()->policy.confirmedState(null));
    }
    @Test void longProcessingIsBoundedAndPollFrequencyBacksOff() {
        RefundPolicy policy=new RefundPolicy(60,86400);
        assertFalse(policy.needsReview(task(20,86399),now));
        assertTrue(policy.needsReview(task(21,86400),now));
        assertEquals(60,policy.pollDelay(task(2,0),now));
        assertEquals(300,policy.pollDelay(task(3,300),now));
        assertEquals(1800,policy.pollDelay(task(30,86400),now));
    }
    @Test void onlyTheDocumentedRefundNotFoundResponsePermitsSameNumberResubmission() {
        String result=ReflectionTestUtils.invokeMethod(WeChatPayUtil.class,"refundQueryResponse",404,"{\"code\":\"RESOURCE_NOT_EXISTS\"}");
        assertEquals("NOT_FOUND",JSON.parseObject(result).getString("status"));
        assertThrows(Exception.class,()->ReflectionTestUtils.invokeMethod(WeChatPayUtil.class,"refundQueryResponse",404,"{\"code\":\"MCH_NOT_EXISTS\"}"));
        assertThrows(Exception.class,()->ReflectionTestUtils.invokeMethod(WeChatPayUtil.class,"refundQueryResponse",500,"{\"code\":\"SYSTEM_ERROR\"}"));
    }
}
