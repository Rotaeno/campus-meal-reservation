package com.sky.mapper;

import com.github.pagehelper.Page;
import com.sky.dto.GoodsSalesDTO;
import com.sky.dto.OrdersPageQueryDTO;
import com.sky.entity.Orders;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;
import org.apache.ibatis.annotations.Update;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;

@Mapper
public interface OrderMapper {
    @Select("select * from orders where user_id=#{userId} and request_key=#{key} for update")
    Orders getByRequest(@Param("userId") Long userId, @Param("key") String key);

    @Select("select * from orders where id=#{id} for update")
    Orders getByIdForUpdate(Long id);

    @Select("select * from orders where number=#{number} for update")
    Orders getByNumberForUpdate(String number);

    @Select("select * from orders where id=#{id} and user_id=#{userId}")
    Orders getOwned(@Param("id") Long id, @Param("userId") Long userId);

    @Select("select * from orders where pickup_code=#{code} for update")
    Orders getByPickupCodeForUpdate(String code);

    @Select("select * from orders where meal_session_id=#{sessionId} order by order_time desc limit 500")
    List<Orders> getBySession(Long sessionId);

    @Update("update orders set status=#{next}, delivery_time=case when #{next}=5 then #{now} else delivery_time end " +
            "where id=#{id} and status=#{expected} and pay_status=1")
    int transition(@Param("id") Long id, @Param("expected") Integer expected,
                   @Param("next") Integer next, @Param("now") LocalDateTime now);

    @Update("update orders set status=2,pay_status=1,checkout_time=#{now} where id=#{id} and status=1 and pay_status=0")
    int markPaid(@Param("id") Long id, @Param("now") LocalDateTime now);

    @Update("update orders set status=6,pay_status=1,refund_status='PENDING',checkout_time=#{now}," +
            "cancel_time=coalesce(cancel_time,#{now}),cancel_reason='超时或取消后支付，等待退款' " +
            "where id=#{id} and status in (1,6) and pay_status=0")
    int markLatePaid(@Param("id") Long id, @Param("now") LocalDateTime now);

    @Update("update orders set status=6,cancel_reason=#{reason},cancel_time=#{now}," +
            "refund_status=case when pay_status=1 then 'PENDING' else 'NONE' end " +
            "where id=#{id} and status=#{expected} and pay_status=#{payStatus}")
    int cancelIfMatches(@Param("id") Long id, @Param("expected") Integer expected,
                        @Param("payStatus") Integer payStatus, @Param("reason") String reason,
                        @Param("now") LocalDateTime now);

    @Select("select o.id from orders o left join meal_session m on o.meal_session_id=m.id " +
            "where o.status=1 and o.pay_status=0 and (o.order_time<=#{before} or m.cutoff_time<=#{now}) limit 100")
    List<Long> findExpired(@Param("before") LocalDateTime before, @Param("now") LocalDateTime now);

    @Update("update orders set pay_status=2,refund_status='SUCCEEDED' where id=#{id} and status=6 and pay_status=1")
    int markRefunded(Long id);

    @Update("update orders set refund_status='FAILED' where id=#{id} and refund_status='PENDING'")
    void markRefundFailed(Long id);

    @Update("update orders set refund_status='PENDING' where id=#{id} and refund_status='FAILED'")
    void markRefundPending(Long id);
    /**
     * 插入订单数据
     * @param order
     */
    void insert(Orders order);

    /**
     * 根据订单号和用户id查询订单
     * @param orderNumber
     * @param userId
     * @return
     */
    Orders getByNumberAndUserId(@Param("orderNumber") String orderNumber, @Param("userId") Long userId);

    /**
     * 修改订单信息
     * @param orders
     */
    void update(Orders orders);

    /**
     * 分页条件查询
     * @param ordersPageQueryDTO
     * @return
     */
    Page<Orders> pageQuery(OrdersPageQueryDTO ordersPageQueryDTO);

    /**
     * 根据id查询订单
     * @param id
     * @return
     */
    Orders getById(Long id);

    /**
     * 根据状态，分别查询出接待单，待派送、派送中的订单数量
     * @param status
     * @return
     */
    Integer countStatus(Integer status);

    /**
     * 根据状态和下单时间查询订单
     * @param status
     * @param orderTime
     */
    List<Orders> getByStatusAndOrderTime(@Param("status") Integer status, @Param("orderTime") LocalDateTime orderTime);

    /**
     * 根据动态条件统计营业额
     * @param map
     * @return
     */
    Double sumByMap(Map map);

    /**
     * 根据动态条件统计订单数量
     * @param map
     * @return
     */
    Integer countByMap(Map map);

    /**
     * 查询商品销量排名
     * @param begin
     * @param end
     */
    List<GoodsSalesDTO> getSalesTop10(LocalDateTime begin, LocalDateTime end);
}
