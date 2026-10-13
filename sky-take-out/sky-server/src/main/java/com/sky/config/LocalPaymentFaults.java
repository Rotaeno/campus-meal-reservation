package com.sky.config;

import org.springframework.context.annotation.Profile;
import org.springframework.stereotype.Component;
import java.util.concurrent.atomic.AtomicInteger;

@Component @Profile("local")
public class LocalPaymentFaults {
    private final AtomicInteger remaining=new AtomicInteger();
    public void failNext(int count) {
        if(count<0 || count>10) { throw new IllegalArgumentException("Expected 0 to 10 failures"); }
        remaining.set(count);
    }
    public boolean consumeFailure() { return remaining.getAndUpdate(value -> Math.max(0,value-1))>0; }
}
