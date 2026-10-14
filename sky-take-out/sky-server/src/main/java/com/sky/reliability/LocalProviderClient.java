package com.sky.reliability;

import com.alibaba.fastjson.JSON;
import com.alibaba.fastjson.JSONObject;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Profile;
import org.springframework.stereotype.Component;
import java.io.*;
import java.net.*;
import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;

/** Independent durable mock ledger. Only a loopback URL is accepted; never a real merchant endpoint. */
@Component @Profile("local")
@ConditionalOnProperty(name="sky.local.provider.url")
public class LocalProviderClient {
    private final String base;
    private final int timeout;
    public LocalProviderClient(@Value("${sky.local.provider.url}") String base,
                               @Value("${sky.local.provider.read-timeout-ms:10000}") int timeout) {
        URI uri = URI.create(base);
        if (!"http".equals(uri.getScheme()) || !"127.0.0.1".equals(uri.getHost()) || uri.getPort() <= 0
                || uri.getUserInfo() != null || uri.getQuery() != null || !"".equals(uri.getPath()) || timeout < 100 || timeout > 30000) {
            throw new IllegalArgumentException("Mock provider must use http://127.0.0.1:port");
        }
        this.base = base; this.timeout = timeout;
    }
    public void paid(String number, BigDecimal total) {
        JSONObject body = new JSONObject(); body.put("number", number); body.put("total", total);
        request("/pay", body);
    }
    public String refund(String number, String refundNumber, BigDecimal refund, BigDecimal total) {
        JSONObject body = new JSONObject(); body.put("number", number); body.put("refundNumber", refundNumber);
        body.put("refund", refund); body.put("total", total); return request("/refund", body);
    }
    public String query(String refundNumber) {
        JSONObject body = new JSONObject(); body.put("refundNumber", refundNumber); return request("/query", body);
    }
    private String request(String path, JSONObject body) {
        HttpURLConnection connection = null;
        try {
            connection = (HttpURLConnection) new URL(base + path).openConnection();
            connection.setInstanceFollowRedirects(false); connection.setConnectTimeout(1000); connection.setReadTimeout(timeout);
            connection.setRequestMethod("POST"); connection.setDoOutput(true); connection.setRequestProperty("Content-Type", "application/json");
            try (OutputStream out = connection.getOutputStream()) { out.write(JSON.toJSONBytes(body)); }
            if (connection.getResponseCode() != 200) { throw new IOException("Mock provider rejected request"); }
            ByteArrayOutputStream data = new ByteArrayOutputStream();
            try (InputStream in = connection.getInputStream()) {
                byte[] buffer = new byte[1024]; int size;
                while ((size = in.read(buffer)) != -1) {
                    if (data.size() + size > 65536) { throw new IOException("Mock provider response too large"); }
                    data.write(buffer, 0, size);
                }
            }
            return new String(data.toByteArray(), StandardCharsets.UTF_8);
        } catch (IOException failure) { throw new IllegalStateException("Local provider transport failed", failure); }
        finally { if (connection != null) { connection.disconnect(); } }
    }
}
