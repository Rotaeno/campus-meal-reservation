ALTER TABLE refund_task
    ADD COLUMN provider_state VARCHAR(16) NOT NULL DEFAULT 'UNKNOWN',
    ADD COLUMN provider_checked_time DATETIME NULL;

-- Existing local execution failures do not establish an external funds failure.
UPDATE refund_task SET provider_state=CASE
    WHEN status='SUCCEEDED' THEN 'SUCCEEDED'
    WHEN provider_submitted=1 THEN 'PROCESSING'
    ELSE 'UNKNOWN' END;

CREATE TABLE refund_review (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    task_id BIGINT NOT NULL,
    operator_id BIGINT NOT NULL,
    reason VARCHAR(200) NOT NULL,
    previous_provider_state VARCHAR(16) NOT NULL,
    previous_failures INT NOT NULL,
    create_time DATETIME NOT NULL,
    INDEX idx_refund_review_task (task_id,id),
    CONSTRAINT fk_refund_review_task FOREIGN KEY (task_id) REFERENCES refund_task(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
