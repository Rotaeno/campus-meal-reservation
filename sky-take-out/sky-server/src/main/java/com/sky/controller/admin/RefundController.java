package com.sky.controller.admin;

import com.sky.entity.RefundTask;
import com.sky.entity.RefundReview;
import com.sky.dto.RefundRetryDTO;
import com.sky.result.Result;
import com.sky.service.RefundService;
import org.springframework.web.bind.annotation.*;
import java.util.List;

@RestController @RequestMapping("/admin/refunds")
public class RefundController {
    private final RefundService service;
    public RefundController(RefundService service) { this.service=service; }
    @GetMapping public Result<List<RefundTask>> list() { return Result.success(service.list()); }
    @PostMapping("/{id}/retry") public Result<String> retry(@PathVariable Long id,@RequestBody RefundRetryDTO request) {
        service.retry(id,request.getReason()); return Result.success();
    }
    @GetMapping("/{id}/reviews") public Result<List<RefundReview>> reviews(@PathVariable Long id) { return Result.success(service.reviews(id)); }
}
