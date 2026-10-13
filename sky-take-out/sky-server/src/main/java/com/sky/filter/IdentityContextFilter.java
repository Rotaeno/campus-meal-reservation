package com.sky.filter;

import com.sky.context.BaseContext;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;
import javax.servlet.*;
import javax.servlet.http.*;
import java.io.IOException;

@Component @Order(Ordered.HIGHEST_PRECEDENCE)
public class IdentityContextFilter extends OncePerRequestFilter {
    @Override protected void doFilterInternal(HttpServletRequest request,HttpServletResponse response,FilterChain chain) throws ServletException,IOException {
        BaseContext.removeCurrentId();
        try { chain.doFilter(request,response); } finally { BaseContext.removeCurrentId(); }
    }
}
