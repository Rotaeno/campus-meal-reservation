package com.sky.controller.user;

import com.sky.entity.MealSession;
import com.sky.result.Result;
import com.sky.service.MealSessionService;
import org.springframework.web.bind.annotation.*;
import java.time.LocalDateTime;
import java.util.List;

@RestController("userMealSessionController")
@RequestMapping("/user/meal")
public class MealSessionController {
    private final MealSessionService service;
    public MealSessionController(MealSessionService service) { this.service=service; }
    @GetMapping("/sessions") public Result<List<MealSession>> list() { return Result.success(service.listOpen()); }
    @GetMapping("/time") public Result<LocalDateTime> time() { return Result.success(service.now()); }
}
