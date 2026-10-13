-- Day-01 SQL stores the teaching password in plaintext; final code compares MD5.
-- Only migrate the exact original demo seed. Re-running this is a no-op.
UPDATE employee
SET password = MD5('123456')
WHERE username = 'admin' AND password = '123456';
