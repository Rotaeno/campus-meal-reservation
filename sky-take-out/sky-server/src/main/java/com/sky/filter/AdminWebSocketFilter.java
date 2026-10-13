package com.sky.filter;

import com.sky.constant.JwtClaimsConstant;
import com.sky.properties.JwtProperties;
import com.sky.utils.JwtUtil;
import io.jsonwebtoken.Claims;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;
import javax.servlet.*;
import javax.servlet.http.*;
import java.io.IOException;

/** JWT is carried in a subprotocol header, never in a logged query string. */
@Component
public class AdminWebSocketFilter extends OncePerRequestFilter {
    private final JwtProperties properties;
    public AdminWebSocketFilter(JwtProperties properties) { this.properties=properties; }
    @Override protected boolean shouldNotFilter(HttpServletRequest request) { return !request.getRequestURI().startsWith("/ws/"); }
    @Override protected void doFilterInternal(HttpServletRequest request,HttpServletResponse response,FilterChain chain) throws ServletException,IOException {
        String protocol=request.getHeader("Sec-WebSocket-Protocol"); String token=null;
        if (protocol!=null) { for(String part:protocol.split(",")) { if(part.trim().startsWith("auth.")) { token=part.trim().substring(5); } } }
        try {
            Claims claims=JwtUtil.parseJWT(properties.getAdminSecretKey(),token);
            if(claims.get(JwtClaimsConstant.EMP_ID)==null) { throw new IllegalArgumentException("Wrong token kind"); }
        } catch(Exception exception) { response.setStatus(401); return; }
        chain.doFilter(request,response);
    }
}
