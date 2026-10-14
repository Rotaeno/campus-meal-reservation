-- The recovery branch filters by status + lease_until; next_retry_time between them
-- in the original index prevented a selective range scan for expired PROCESSING leases.
ALTER TABLE refund_task ADD INDEX idx_refund_lease (status, lease_until);
