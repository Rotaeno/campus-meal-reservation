package com.sky.service;

import com.sky.context.BaseContext;
import com.sky.exception.OrderBusinessException;
import com.sky.mapper.UserMapper;
import org.springframework.stereotype.Service;

/** The durable per-user row serializes cart writes and checkout across instances. */
@Service
public class UserLockService {
    private final UserMapper users;
    public UserLockService(UserMapper users) { this.users = users; }
    public Long lockCurrentUser() {
        Long id = BaseContext.getCurrentId();
        if (id == null || users.lockById(id) == null) { throw new OrderBusinessException("请先登录"); }
        return id;
    }
}
