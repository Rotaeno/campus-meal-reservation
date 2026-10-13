package com.sky.service;

import com.sky.properties.WeChatProperties;
import com.wechat.pay.contrib.apache.httpclient.util.PemUtil;
import org.springframework.stereotype.Component;
import javax.servlet.http.HttpServletRequest;
import java.io.FileInputStream;
import java.math.BigInteger;
import java.nio.charset.StandardCharsets;
import java.security.*;
import java.security.cert.X509Certificate;
import java.time.Clock;
import java.util.Base64;
import java.util.Date;

@Component
public class WeChatNotificationVerifier {
    private final WeChatProperties properties;
    private final Clock clock;
    public WeChatNotificationVerifier(WeChatProperties properties,Clock clock) { this.properties=properties; this.clock=clock; }
    public void verify(HttpServletRequest request,String body) throws Exception {
        String serial=request.getHeader("Wechatpay-Serial");
        if(serial==null || !serial.matches("[0-9a-fA-F]{1,64}")) { throw new GeneralSecurityException("Unknown certificate"); }
        try(FileInputStream input=new FileInputStream(properties.getWeChatPayCertFilePath())) {
            X509Certificate certificate=PemUtil.loadCertificate(input); certificate.checkValidity(Date.from(clock.instant()));
            if(!certificate.getSerialNumber().equals(new BigInteger(serial,16))) { throw new GeneralSecurityException("Unknown certificate"); }
            verifySignature(certificate.getPublicKey(),request.getHeader("Wechatpay-Timestamp"),request.getHeader("Wechatpay-Nonce"),
                    request.getHeader("Wechatpay-Signature"),body,clock);
        }
    }
    public static void verifySignature(PublicKey key,String timestamp,String nonce,String signature,String body,Clock clock) throws Exception {
        if(timestamp==null || nonce==null || signature==null || body==null || body.length()>65536
                || nonce.isEmpty() || nonce.length()>128 || nonce.contains("\n") || nonce.contains("\r")
                || !timestamp.matches("[0-9]{10,12}") || Math.abs(clock.instant().getEpochSecond()-Long.parseLong(timestamp))>300) {
            throw new GeneralSecurityException("Invalid notification headers");
        }
        Signature verifier=Signature.getInstance("SHA256withRSA"); verifier.initVerify(key);
        verifier.update((timestamp+"\n"+nonce+"\n"+body+"\n").getBytes(StandardCharsets.UTF_8));
        if(!verifier.verify(Base64.getDecoder().decode(signature))) { throw new GeneralSecurityException("Invalid notification signature"); }
    }
}
