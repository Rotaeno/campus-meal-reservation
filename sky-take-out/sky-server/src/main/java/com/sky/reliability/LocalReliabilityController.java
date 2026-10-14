package com.sky.reliability;

import com.sky.result.Result;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Profile;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.bind.annotation.*;
import java.util.*;

/** Admin JWT required by the existing interceptor; absent unless local AND explicitly enabled. */
@RestController @Profile("local")
@ConditionalOnProperty(name="sky.local.reliability.enabled", havingValue="true")
@RequestMapping("/admin/local/reliability")
public class LocalReliabilityController {
    private final ReliabilityProbe probes;
    private final DatabaseRetry retry;
    private final JdbcTemplate jdbc;
    private final com.alibaba.druid.pool.DruidDataSource pool;
    private final com.sky.service.RefundService refunds;
    public LocalReliabilityController(ReliabilityProbe probes, DatabaseRetry retry, JdbcTemplate jdbc,
                                      com.alibaba.druid.pool.DruidDataSource pool, com.sky.service.RefundService refunds) {
        this.probes = probes; this.retry = retry; this.jdbc = jdbc; this.pool = pool;
        this.refunds = refunds;
    }
    @PostMapping("/gates/{id}") public Result<String> arm(@PathVariable String id, @RequestBody Map<String,String> request) {
        probes.arm(id, request.get("stage"), request.get("key")); return Result.success();
    }
    @GetMapping("/gates/{id}") public Result<ReliabilityProbe.Gate> get(@PathVariable String id) { return Result.success(probes.get(id)); }
    @PostMapping("/gates/{id}/release") public Result<String> release(@PathVariable String id) { probes.release(id); return Result.success(); }
    @PostMapping("/refunds/{id}/run") public Result<String> refund(@PathVariable Long id) { refunds.process(id); return Result.success(); }
    @GetMapping("/configuration") public Result<Map<String,Object>> configuration() {
        return Result.success(jdbc.queryForMap("select DATABASE() as databaseName, @@session.innodb_lock_wait_timeout as lockWaitSeconds"));
    }
    @GetMapping("/metrics") public Result<Map<String,Object>> metrics() {
        Map<String,Object> result = new LinkedHashMap<>();
        result.put("dbRetries", retry.getRetries()); result.put("dbRetryExhausted", retry.getExhausted());
        result.put("poolActive", pool.getActiveCount()); result.put("poolMax", pool.getMaxActive());
        result.put("poolWaitThreads", pool.getWaitThreadCount()); result.put("poolWaitCount", pool.getNotEmptyWaitCount());
        result.put("poolWaitMillis", pool.getNotEmptyWaitMillis());
        return Result.success(result);
    }
    // Table created only inside the runner's disposable schema. No arbitrary SQL or table name input.
    @PostMapping("/db-probe") @RetryDatabaseTransaction @Transactional
    public Result<String> dbProbe(@RequestBody Map<String,String> request) {
        jdbc.update("update reliability_probe set value=value+1 where id=1");
        probes.hit("dbProbe.afterWrite", request.get("key"));
        jdbc.update("update reliability_probe set value=value+1 where id=2");
        return Result.success();
    }
}
