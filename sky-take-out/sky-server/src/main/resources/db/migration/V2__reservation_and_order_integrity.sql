CREATE TABLE meal_session (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    title VARCHAR(64) NOT NULL,
    meal_date DATE NOT NULL,
    cutoff_time DATETIME NOT NULL,
    pickup_start DATETIME NOT NULL,
    pickup_end DATETIME NOT NULL,
    capacity INT NOT NULL,
    reserved_count INT NOT NULL DEFAULT 0,
    status INT NOT NULL DEFAULT 1,
    create_time DATETIME NOT NULL,
    CONSTRAINT chk_meal_capacity CHECK (capacity > 0 AND reserved_count >= 0 AND reserved_count <= capacity),
    INDEX idx_meal_date (meal_date, status, cutoff_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

ALTER TABLE user ADD UNIQUE KEY uk_user_openid (openid);

ALTER TABLE orders
    ADD COLUMN request_key VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
    ADD COLUMN request_hash VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
    ADD COLUMN fulfillment_type INT NOT NULL DEFAULT 1,
    ADD COLUMN meal_session_id BIGINT NULL,
    ADD COLUMN reserved_quantity INT NOT NULL DEFAULT 0,
    ADD COLUMN pickup_code VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL,
    ADD COLUMN delivery_fee DECIMAL(10,2) NOT NULL DEFAULT 0,
    ADD COLUMN refund_status VARCHAR(16) NOT NULL DEFAULT 'NONE',
    ADD CONSTRAINT fk_order_session FOREIGN KEY (meal_session_id) REFERENCES meal_session(id),
    ADD UNIQUE KEY uk_order_request (user_id, request_key),
    ADD UNIQUE KEY uk_order_number (number),
    ADD UNIQUE KEY uk_order_pickup (pickup_code),
    ADD INDEX idx_order_timeout (status, pay_status, order_time),
    ADD INDEX idx_order_session (meal_session_id, status);

CREATE TABLE refund_task (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    order_id BIGINT NOT NULL,
    refund_number VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'PENDING',
    provider_submitted TINYINT NOT NULL DEFAULT 0,
    attempts INT NOT NULL DEFAULT 0,
    failures INT NOT NULL DEFAULT 0,
    next_retry_time DATETIME NOT NULL,
    lease_until DATETIME NULL,
    lease_token VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL,
    last_error VARCHAR(256) NULL,
    create_time DATETIME NOT NULL,
    update_time DATETIME NOT NULL,
    UNIQUE KEY uk_refund_order (order_id),
    UNIQUE KEY uk_refund_number (refund_number),
    CONSTRAINT fk_refund_order FOREIGN KEY (order_id) REFERENCES orders(id),
    INDEX idx_refund_retry (status, next_retry_time, lease_until)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
