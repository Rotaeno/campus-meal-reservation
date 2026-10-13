package com.sky.entity;

import lombok.Data;
import java.time.LocalDate;
import java.time.LocalDateTime;

@Data
public class MealSession {
    private Long id;
    private String title;
    private LocalDate mealDate;
    private LocalDateTime cutoffTime;
    private LocalDateTime pickupStart;
    private LocalDateTime pickupEnd;
    private Integer capacity;
    private Integer reservedCount;
    private Integer status;
    private LocalDateTime createTime;
}
