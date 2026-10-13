package com.sky.entity;

import lombok.Data;
import java.time.LocalDateTime;

@Data
public class RefundTask {
    private Long id;
    private Long orderId;
    private String refundNumber;
    private String status;
    private Boolean providerSubmitted;
    private Integer attempts;
    private Integer failures;
    private LocalDateTime nextRetryTime;
    private LocalDateTime leaseUntil;
    private String leaseToken;
    private String lastError;
    private LocalDateTime createTime;
    private LocalDateTime updateTime;
}
