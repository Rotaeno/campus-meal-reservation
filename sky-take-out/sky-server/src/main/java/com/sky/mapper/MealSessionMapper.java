package com.sky.mapper;

import com.sky.entity.MealSession;
import org.apache.ibatis.annotations.*;
import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;

@Mapper
public interface MealSessionMapper {
    @Insert("insert into meal_session(title,meal_date,cutoff_time,pickup_start,pickup_end,capacity,reserved_count,status,create_time) " +
            "values(#{title},#{mealDate},#{cutoffTime},#{pickupStart},#{pickupEnd},#{capacity},0,1,#{createTime})")
    @Options(useGeneratedKeys=true,keyProperty="id")
    void insert(MealSession session);

    @Select("select * from meal_session where id=#{id}")
    MealSession getById(Long id);

    @Select("select * from meal_session order by meal_date desc,id desc limit 100")
    List<MealSession> listAll();

    @Select("select * from meal_session where status=1 and cutoff_time>#{now} order by meal_date,pickup_start limit 50")
    List<MealSession> listOpen(LocalDateTime now);

    @Update("update meal_session set reserved_count=reserved_count+#{quantity} where id=#{id} and status=1 " +
            "and cutoff_time>#{now} and reserved_count+#{quantity}<=capacity")
    int reserve(@Param("id") Long id,@Param("quantity") Integer quantity,@Param("now") LocalDateTime now);

    @Update("update meal_session set reserved_count=reserved_count-#{quantity} where id=#{id} and reserved_count>=#{quantity}")
    int release(@Param("id") Long id,@Param("quantity") Integer quantity);

    @Update("update meal_session set status=#{status} where id=#{id}")
    int setStatus(@Param("id") Long id,@Param("status") Integer status);

    @Select("select d.name, sum(d.number) quantity from order_detail d join orders o on d.order_id=o.id " +
            "where o.meal_session_id=#{id} and o.status in (2,3,4) and o.pay_status=1 group by d.name order by quantity desc")
    List<Map<String,Object>> preparationList(Long id);
}
