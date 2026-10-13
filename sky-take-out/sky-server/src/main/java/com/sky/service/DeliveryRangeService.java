package com.sky.service;

import com.alibaba.fastjson.JSON;
import com.alibaba.fastjson.JSONObject;
import com.sky.exception.OrderBusinessException;
import com.sky.utils.HttpClientUtil;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.core.env.Environment;
import org.springframework.core.env.Profiles;
import org.springframework.stereotype.Service;
import java.util.HashMap;
import java.util.Map;

@Service
public class DeliveryRangeService {
    private final Environment environment;
    private final String shopAddress;
    private final String ak;
    public DeliveryRangeService(Environment environment,@Value("${sky.shop.address}") String shopAddress,@Value("${sky.baidu.ak}") String ak) {
        this.environment=environment; this.shopAddress=shopAddress; this.ak=ak;
    }
    public void check(String address) {
        if (environment.acceptsProfiles(Profiles.of("local"))) { return; }
        Map<String,String> parameters=new HashMap<>();
        parameters.put("ak",ak); parameters.put("output","json");
        String origin=coordinates(shopAddress,parameters);
        String destination=coordinates(address,parameters);
        parameters.remove("address"); parameters.put("origin",origin); parameters.put("destination",destination); parameters.put("steps_info","0");
        JSONObject route=JSON.parseObject(HttpClientUtil.doGet("https://api.map.baidu.com/directionlite/v1/driving",parameters));
        if (route==null || !"0".equals(route.getString("status")) || route.getJSONObject("result")==null
                || route.getJSONObject("result").getJSONArray("routes").isEmpty()) {
            throw new OrderBusinessException("配送线路规划失败");
        }
        if (route.getJSONObject("result").getJSONArray("routes").getJSONObject(0).getIntValue("distance")>5000) {
            throw new OrderBusinessException("超出配送范围");
        }
    }
    private String coordinates(String address,Map<String,String> parameters) {
        parameters.put("address",address);
        JSONObject response=JSON.parseObject(HttpClientUtil.doGet("https://api.map.baidu.com/geocoding/v3",parameters));
        if (response==null || !"0".equals(response.getString("status"))) { throw new OrderBusinessException("地址解析失败"); }
        JSONObject location=response.getJSONObject("result").getJSONObject("location");
        return location.getString("lat")+","+location.getString("lng");
    }
}
