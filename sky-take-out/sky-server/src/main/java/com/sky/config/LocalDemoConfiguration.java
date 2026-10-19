package com.sky.config;

import com.alibaba.fastjson.JSONObject;
import com.sky.dto.UserLoginDTO;
import com.sky.entity.User;
import com.sky.exception.LoginFailedException;
import com.sky.mapper.UserMapper;
import com.sky.service.UserService;
import com.sky.utils.AliOssUtil;
import com.sky.utils.WeChatPayUtil;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.beans.factory.ObjectProvider;
import com.sky.reliability.LocalProviderClient;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Primary;
import org.springframework.context.annotation.Profile;

import java.io.IOException;
import java.math.BigDecimal;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.time.LocalDateTime;
import java.util.UUID;

/** Local demonstration adapters. No real login, payment, refund or OSS calls. */
@Configuration
@Profile("local")
public class LocalDemoConfiguration {
    @Bean
    @Primary
    public UserService localUserService(UserMapper userMapper) {
        return (UserLoginDTO login) -> {
            if (!"local-demo".equals(login.getCode()) && !"local-second".equals(login.getCode())
                    && (login.getCode()==null || !login.getCode().matches("local-test-[a-z0-9-]{1,20}"))) {
                throw new LoginFailedException("本地演示请使用 code=local-demo 或 local-second");
            }
            String openid = "sky-demo-" + login.getCode();
            User user = userMapper.getByOpenId(openid);
            if (user == null) {
                user = User.builder().openid(openid).name("本地演示用户")
                        .createTime(LocalDateTime.now()).build();
                try { userMapper.insert(user); }
                catch (org.springframework.dao.DuplicateKeyException duplicate) { user=userMapper.getByOpenId(openid); }
            }
            return user;
        };
    }

    @Bean
    @Primary
    public WeChatPayUtil localWeChatPayUtil(LocalPaymentFaults faults, ObjectProvider<LocalProviderClient> provider) {
        return new WeChatPayUtil() {
            @Override
            public JSONObject pay(String number, BigDecimal total, String description, String openid) {
                JSONObject response = new JSONObject();
                response.put("timeStamp", String.valueOf(System.currentTimeMillis() / 1000));
                response.put("nonceStr", UUID.randomUUID().toString());
                response.put("package", "prepay_id=LOCAL_SIMULATION_" + number);
                response.put("signType", "LOCAL_SIMULATION");
                response.put("paySign", "LOCAL_SIMULATION_NO_REAL_PAYMENT");
                return response;
            }

            @Override
            public String refund(String number, String refundNumber, BigDecimal refund, BigDecimal total) {
                if (faults.consumeFailure()) { throw new IllegalStateException("Local refund fault injection"); }
                if (refund == null || total == null || refund.signum() <= 0 || refund.compareTo(total) > 0) {
                    throw new IllegalArgumentException("Invalid simulated refund amount");
                }
                LocalProviderClient ledger = provider.getIfAvailable();
                if (ledger != null) { return ledger.refund(number, refundNumber, refund, total); }
                return "{\"status\":\"LOCAL_SIMULATION\"}";
            }
            @Override public String queryRefund(String number) {
                LocalProviderClient ledger = provider.getIfAvailable();
                // The lightweight demo has no durable external ledger to query.
                // Do not turn a failed first submission into a fabricated success.
                return ledger == null ? "{\"status\":\"NOT_FOUND\"}" : ledger.query(number);
            }
        };
    }

    @Bean
    @Primary
    public AliOssUtil localAliOssUtil(@Value("${SKY_UPLOAD_DIR:./.local/uploads}") String directory) {
        return new AliOssUtil("local-demo", "local-demo", "local-demo", "local-demo") {
            @Override
            public String upload(byte[] bytes, String objectName) {
                Path root = Paths.get(directory).toAbsolutePath().normalize();
                Path target = root.resolve(objectName).normalize();
                if (!target.startsWith(root)) {
                    throw new IllegalArgumentException("Invalid upload name");
                }
                try {
                    Files.createDirectories(root);
                    Files.write(target, bytes);
                } catch (IOException exception) {
                    throw new IllegalStateException("Local upload failed", exception);
                }
                return "/local-files/" + objectName;
            }
        };
    }
}
