-- User-scoped cart reads/deletes must not scan and lock other users' rows.
CREATE INDEX idx_cart_user_id ON shopping_cart (user_id, id);
CREATE INDEX idx_address_user_default ON address_book (user_id, is_default, id);
