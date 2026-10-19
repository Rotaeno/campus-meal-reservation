package com.sky.entity;

import lombok.Data;
import java.time.LocalDateTime;

@Data
public class RefundReview {
    private Long id;
    private Long taskId;
    private Long operatorId;
    private String reason;
    private String previousProviderState;
    private Integer previousFailures;
    private LocalDateTime createTime;
}
