package com.sky.mapper;

import com.sky.entity.RefundTask;
import com.sky.entity.RefundReview;
import org.apache.ibatis.annotations.*;
import java.time.LocalDateTime;
import java.util.List;

@Mapper
public interface RefundTaskMapper {
    @Insert("insert into refund_task(order_id,refund_number,status,next_retry_time,create_time,update_time) " +
            "values(#{orderId},#{refundNumber},'PENDING',#{now},#{now},#{now}) on duplicate key update order_id=order_id")
    void enqueue(@Param("orderId") Long orderId,@Param("refundNumber") String refundNumber,@Param("now") LocalDateTime now);

    @Select("select * from refund_task where id=#{id}")
    RefundTask getById(Long id);

    @Select("select * from refund_task where id=#{id} for update")
    RefundTask getByIdForUpdate(Long id);

    @Select("select * from refund_review where task_id=#{id} order by id desc limit 50")
    List<RefundReview> reviews(Long id);

    @Insert("insert into refund_review(task_id,operator_id,reason,previous_provider_state,previous_failures,create_time) " +
            "values(#{taskId},#{operatorId},#{reason},#{previousProviderState},#{previousFailures},#{createTime})")
    void recordReview(RefundReview review);

    @Select("select * from refund_task order by id desc limit 100")
    List<RefundTask> listAll();

    @Select("(select id from refund_task where status='PENDING' and next_retry_time<=#{now} order by next_retry_time limit 20) " +
            "union all (select id from refund_task where status='PROCESSING' and lease_until<#{now} order by lease_until limit 20) " +
            "order by id limit 20")
    List<Long> due(LocalDateTime now);

    @Select("select id from refund_task where status='PENDING' and next_retry_time<=#{now} " +
            "order by next_retry_time limit 20 for update skip locked")
    List<Long> pendingCandidates(LocalDateTime now);

    @Select("select id from refund_task where status='PROCESSING' and lease_until<#{now} " +
            "order by lease_until limit 20 for update skip locked")
    List<Long> expiredLeaseCandidates(LocalDateTime now);

    @Update("update refund_task set status='PROCESSING',lease_token=#{token},lease_until=#{until},attempts=attempts+1,update_time=#{now} " +
            "where id=#{id} and ((status='PENDING' and next_retry_time<=#{now}) or (status='PROCESSING' and lease_until<#{now}))")
    int claim(@Param("id") Long id,@Param("token") String token,@Param("until") LocalDateTime until,@Param("now") LocalDateTime now);

    @Update("update refund_task set status='SUCCEEDED',provider_state='SUCCEEDED',provider_checked_time=#{now},last_error=null,lease_until=null,update_time=#{now} " +
            "where id=#{id} and status='PROCESSING' and lease_token=#{token}")
    int succeed(@Param("id") Long id,@Param("token") String token,@Param("now") LocalDateTime now);

    @Update("update refund_task set status=#{status},provider_submitted=#{submitted},failures=#{failures}," +
            "next_retry_time=#{next},lease_until=null,last_error=#{error},provider_state=#{providerState},provider_checked_time=#{now},update_time=#{now} " +
            "where id=#{id} and status='PROCESSING' and lease_token=#{token}")
    int reschedule(@Param("id") Long id,@Param("token") String token,@Param("status") String status,
                   @Param("submitted") Boolean submitted,@Param("failures") Integer failures,
                   @Param("next") LocalDateTime next,@Param("error") String error,@Param("providerState") String providerState,@Param("now") LocalDateTime now);

    @Update("update refund_task set status='PENDING',failures=0,next_retry_time=#{now},last_error=null,update_time=#{now} where id=#{id} and status='FAILED'")
    int retry(@Param("id") Long id,@Param("now") LocalDateTime now);
}
