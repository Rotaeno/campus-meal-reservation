package com.sky.controller;

import com.sky.controller.admin.OrderController;
import com.sky.service.OrderService;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import static org.mockito.Mockito.verify;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@ExtendWith(MockitoExtension.class)
class OrderControllerRegressionTest {
    @Mock private OrderService orderService;
    @InjectMocks private OrderController controller;

    @Test
    void completionUsesTheOrderIdFromTheUrl() throws Exception {
        MockMvcBuilders.standaloneSetup(controller).build()
                .perform(put("/admin/order/complete/42"))
                .andExpect(status().isOk());
        verify(orderService).complete(42L);
    }

    @Test
    void detailLookupUsesTheOrderIdFromTheUrl() throws Exception {
        MockMvcBuilders.standaloneSetup(controller).build()
                .perform(get("/admin/order/details/42"))
                .andExpect(status().isOk());
        verify(orderService).details(42L);
    }
}
