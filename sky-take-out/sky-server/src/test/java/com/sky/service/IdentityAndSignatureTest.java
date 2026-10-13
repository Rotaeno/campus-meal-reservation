package com.sky.service;

import com.sky.context.BaseContext;
import com.sky.filter.IdentityContextFilter;
import org.junit.jupiter.api.*;
import org.springframework.mock.web.*;
import javax.servlet.ServletException;
import java.nio.charset.StandardCharsets;
import java.security.*;
import java.time.*;
import java.util.Base64;
import static org.junit.jupiter.api.Assertions.*;

class IdentityAndSignatureTest {
    private final Clock clock=Clock.fixed(Instant.parse("2026-10-11T04:00:00Z"),ZoneId.of("Asia/Shanghai"));
    private KeyPair pair;
    private final String body="{\"event_type\":\"TRANSACTION.SUCCESS\"}";
    private String timestamp;
    @BeforeEach void setup() throws Exception { KeyPairGenerator generator=KeyPairGenerator.getInstance("RSA"); generator.initialize(2048); pair=generator.generateKeyPair(); timestamp=String.valueOf(clock.instant().getEpochSecond()); }
    private String sign(String content) throws Exception {
        Signature signature=Signature.getInstance("SHA256withRSA"); signature.initSign(pair.getPrivate());
        signature.update((timestamp+"\nnonce-unit-test\n"+content+"\n").getBytes(StandardCharsets.UTF_8));
        return Base64.getEncoder().encodeToString(signature.sign());
    }
    @Test void anAuthenticatedBodyPassesRsaVerification() throws Exception {
        WeChatNotificationVerifier.verifySignature(pair.getPublic(),timestamp,"nonce-unit-test",sign(body),body,clock);
    }
    @Test void changingThePaymentBodyInvalidatesTheSignature() throws Exception {
        String signed=sign(body); assertThrows(GeneralSecurityException.class,()->WeChatNotificationVerifier.verifySignature(pair.getPublic(),timestamp,"nonce-unit-test",signed,body+" ",clock));
    }
    @Test void anOldSignedNotificationIsRejected() throws Exception {
        timestamp=String.valueOf(clock.instant().minusSeconds(301).getEpochSecond()); String signed=sign(body);
        assertThrows(GeneralSecurityException.class,()->WeChatNotificationVerifier.verifySignature(pair.getPublic(),timestamp,"nonce-unit-test",signed,body,clock));
    }
    @Test void missingHeadersAreRejected() {
        assertThrows(GeneralSecurityException.class,()->WeChatNotificationVerifier.verifySignature(pair.getPublic(),null,null,null,body,clock));
    }
    @Test void requestIdentityIsClearedBeforeAndAfterAnException() {
        BaseContext.setCurrentId(999L);
        assertThrows(ServletException.class,()->new IdentityContextFilter().doFilter(new MockHttpServletRequest(),new MockHttpServletResponse(),(request,response)->{
            assertNull(BaseContext.getCurrentId()); BaseContext.setCurrentId(1L); throw new ServletException("unit failure");
        }));
        assertNull(BaseContext.getCurrentId());
    }
}
