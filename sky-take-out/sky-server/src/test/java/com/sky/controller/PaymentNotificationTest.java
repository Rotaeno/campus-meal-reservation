package com.sky.controller;

import com.alibaba.fastjson.JSON;
import com.alibaba.fastjson.JSONObject;
import com.sky.controller.notify.PayNotifyController;
import com.sky.exception.OrderBusinessException;
import com.sky.properties.WeChatProperties;
import com.sky.service.OrderService;
import com.sky.service.WeChatNotificationVerifier;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockHttpServletRequest;
import javax.crypto.Cipher;
import javax.crypto.spec.GCMParameterSpec;
import javax.crypto.spec.SecretKeySpec;
import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.util.Base64;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

/** Encrypted protocol fixtures; this is not a real merchant acceptance test. */
class PaymentNotificationTest {
    private final OrderService orders=mock(OrderService.class);
    private final WeChatNotificationVerifier verifier=mock(WeChatNotificationVerifier.class);
    private final WeChatProperties properties=new WeChatProperties();
    private final MockHttpServletRequest request=new MockHttpServletRequest();
    private PayNotifyController controller;
    private JSONObject payment;
    @BeforeEach void setup() {
        properties.setAppid("fixture-app"); properties.setMchid("fixture-merchant");
        properties.setApiV3Key("0123456789abcdef0123456789abcdef");
        controller=new PayNotifyController(orders,properties,verifier);
        payment=JSON.parseObject("{\"appid\":\"fixture-app\",\"mchid\":\"fixture-merchant\",\"trade_state\":\"SUCCESS\",\"out_trade_no\":\"fixture-order\",\"amount\":{\"total\":1234,\"currency\":\"CNY\"}}");
    }
    private String body() throws Exception {
        String nonce="nonce1234567", associated="transaction";
        Cipher cipher=Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.ENCRYPT_MODE,new SecretKeySpec(properties.getApiV3Key().getBytes(StandardCharsets.UTF_8),"AES"),
                new GCMParameterSpec(128,nonce.getBytes(StandardCharsets.UTF_8)));
        cipher.updateAAD(associated.getBytes(StandardCharsets.UTF_8));
        JSONObject resource=new JSONObject(); resource.put("nonce",nonce); resource.put("associated_data",associated);
        resource.put("ciphertext",Base64.getEncoder().encodeToString(cipher.doFinal(payment.toJSONString().getBytes(StandardCharsets.UTF_8))));
        JSONObject envelope=new JSONObject(); envelope.put("event_type","TRANSACTION.SUCCESS"); envelope.put("resource",resource);
        return envelope.toJSONString();
    }
    @Test void verifiedEncryptedNotificationConvertsCentsAndAcknowledges() throws Exception {
        String body=body();
        assertEquals(204,controller.paySuccessNotify(body,request).getStatusCodeValue());
        verify(verifier).verify(request,body);
        verify(orders).paySuccess("fixture-order",new BigDecimal("12.34"));
    }
    @Test void invalidSignatureNeverDecryptsOrMutatesOrder() throws Exception {
        doThrow(new GeneralSecurityException()).when(verifier).verify(any(),anyString());
        assertEquals(401,controller.paySuccessNotify(body(),request).getStatusCodeValue());
        verifyNoInteractions(orders);
    }
    @Test void wrongAppIdRejected() throws Exception {
        payment.put("appid","another-app");
        assertEquals(400,controller.paySuccessNotify(body(),request).getStatusCodeValue()); verifyNoInteractions(orders);
    }
    @Test void wrongMerchantRejected() throws Exception {
        payment.put("mchid","another-merchant");
        assertEquals(400,controller.paySuccessNotify(body(),request).getStatusCodeValue()); verifyNoInteractions(orders);
    }
    @Test void unsuccessfulTradeRejected() throws Exception {
        payment.put("trade_state","NOTPAY");
        assertEquals(400,controller.paySuccessNotify(body(),request).getStatusCodeValue()); verifyNoInteractions(orders);
    }
    @Test void unsupportedCurrencyRejected() throws Exception {
        payment.getJSONObject("amount").put("currency","USD");
        assertEquals(400,controller.paySuccessNotify(body(),request).getStatusCodeValue()); verifyNoInteractions(orders);
    }
    @Test void tamperedCiphertextRejected() throws Exception {
        JSONObject envelope=JSON.parseObject(body()); envelope.getJSONObject("resource").put("ciphertext","broken");
        assertEquals(401,controller.paySuccessNotify(envelope.toJSONString(),request).getStatusCodeValue()); verifyNoInteractions(orders);
    }
    @Test void amountMismatchIsNotAcknowledgedAsSuccess() throws Exception {
        doThrow(new OrderBusinessException("amount mismatch")).when(orders).paySuccess(anyString(),any());
        assertEquals(400,controller.paySuccessNotify(body(),request).getStatusCodeValue());
    }
}
