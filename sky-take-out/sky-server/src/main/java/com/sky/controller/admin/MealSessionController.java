package com.sky.controller.admin;

import com.sky.entity.MealSession;
import com.sky.entity.Orders;
import com.sky.result.Result;
import com.sky.service.MealSessionService;
import org.springframework.web.bind.annotation.*;
import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/admin/meal")
public class MealSessionController {
    private final MealSessionService service;
    public MealSessionController(MealSessionService service) { this.service=service; }
    @GetMapping("/time") public Result<java.time.LocalDateTime> time() { return Result.success(service.now()); }
    @GetMapping("/sessions") public Result<List<MealSession>> list() { return Result.success(service.listAll()); }
    @PostMapping("/sessions") public Result<MealSession> create(@RequestBody MealSession session) { return Result.success(service.create(session)); }
    @PutMapping("/sessions/{id}/status/{status}") public Result<String> status(@PathVariable Long id,@PathVariable Integer status) { service.setStatus(id,status); return Result.success(); }
    @GetMapping("/sessions/{id}/orders") public Result<List<Orders>> orders(@PathVariable Long id) { return Result.success(service.orders(id)); }
    @GetMapping("/sessions/{id}/preparation") public Result<List<Map<String,Object>>> preparation(@PathVariable Long id) { return Result.success(service.preparation(id)); }
    @PutMapping("/orders/{id}/ready") public Result<String> ready(@PathVariable Long id) { service.ready(id); return Result.success(); }
    @PostMapping("/redeem") public Result<Orders> redeem(@RequestBody Map<String,String> request) { return Result.success(service.redeem(request.get("pickupCode"))); }
}
