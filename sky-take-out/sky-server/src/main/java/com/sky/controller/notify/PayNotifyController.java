package com.sky.controller.notify;

import com.alibaba.fastjson.JSON;
import com.alibaba.fastjson.JSONObject;
import com.sky.exception.OrderBusinessException;
import com.sky.properties.WeChatProperties;
import com.sky.service.OrderService;
import com.sky.service.WeChatNotificationVerifier;
import com.wechat.pay.contrib.apache.httpclient.util.AesUtil;
import org.springframework.context.annotation.Profile;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import javax.servlet.http.HttpServletRequest;
import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;

@RestController @Profile("!local") @RequestMapping("/notify")
public class PayNotifyController {
    private final OrderService orders;
    private final WeChatProperties properties;
    private final WeChatNotificationVerifier verifier;
    public PayNotifyController(OrderService orders,WeChatProperties properties,WeChatNotificationVerifier verifier) {
        this.orders=orders; this.properties=properties; this.verifier=verifier;
    }
    @PostMapping("/paySuccess")
    public ResponseEntity<Void> paySuccessNotify(@RequestBody String body,HttpServletRequest request) {
        final JSONObject payment;
        try {
            verifier.verify(request,body);
            JSONObject envelope=JSON.parseObject(body);
            if(!"TRANSACTION.SUCCESS".equals(envelope.getString("event_type"))) { return ResponseEntity.badRequest().build(); }
            JSONObject resource=envelope.getJSONObject("resource");
            String associated=resource.getString("associated_data");
            String decoded=new AesUtil(properties.getApiV3Key().getBytes(StandardCharsets.UTF_8)).decryptToString(
                    (associated==null?"":associated).getBytes(StandardCharsets.UTF_8),resource.getString("nonce").getBytes(StandardCharsets.UTF_8),resource.getString("ciphertext"));
            payment=JSON.parseObject(decoded);
            if(!properties.getAppid().equals(payment.getString("appid")) || !properties.getMchid().equals(payment.getString("mchid"))
                    || !"SUCCESS".equals(payment.getString("trade_state"))) { return ResponseEntity.badRequest().build(); }
        } catch(Exception exception) { return ResponseEntity.status(401).build(); }
        try {
            JSONObject amount=payment.getJSONObject("amount");
            if(amount==null || !"CNY".equals(amount.getString("currency")) || amount.getBigDecimal("total")==null) { return ResponseEntity.badRequest().build(); }
            orders.paySuccess(payment.getString("out_trade_no"),amount.getBigDecimal("total").movePointLeft(2));
            return ResponseEntity.noContent().build();
        } catch(OrderBusinessException exception) { return ResponseEntity.badRequest().build(); }
    }
}
