-- Delivery keeps its address; campus pickup stores its contact and session instead.
ALTER TABLE orders
    MODIFY COLUMN address_book_id BIGINT NULL COMMENT 'Delivery address; null for campus pickup',
    ADD CONSTRAINT chk_order_fulfillment CHECK (
        (fulfillment_type=1 AND address_book_id IS NOT NULL AND meal_session_id IS NULL AND reserved_quantity=0)
        OR
        (fulfillment_type=2 AND address_book_id IS NULL AND meal_session_id IS NOT NULL
         AND reserved_quantity>0 AND pickup_code IS NOT NULL)
    );
