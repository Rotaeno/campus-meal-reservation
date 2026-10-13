package com.sky.controller.admin;

import com.sky.config.LocalPaymentFaults;
import com.sky.result.Result;
import com.sky.service.OrderService;
import com.sky.service.RefundService;
import org.springframework.context.annotation.Profile;
import org.springframework.web.bind.annotation.*;
import java.util.Map;

@RestController @Profile("local") @RequestMapping("/admin/local")
public class LocalDiagnosticsController {
    private final OrderService orders;
    private final RefundService refunds;
    private final LocalPaymentFaults faults;
    public LocalDiagnosticsController(OrderService orders,RefundService refunds,LocalPaymentFaults faults) {
        this.orders=orders; this.refunds=refunds; this.faults=faults;
    }
    @PostMapping("/timeouts/{id}") public Result<Boolean> expire(@PathVariable Long id) { return Result.success(orders.expireOrder(id)); }
    @PostMapping("/refunds/run") public Result<String> runRefunds() { refunds.processDue(); return Result.success(); }
    @PostMapping("/refunds/fail-next") public Result<String> fail(@RequestBody Map<String,Integer> request) {
        Integer count=request.get("count");
        if(count==null || count<0 || count>10) { return Result.error("失败次数需为0至10"); }
        faults.failNext(count); return Result.success();
    }
}
