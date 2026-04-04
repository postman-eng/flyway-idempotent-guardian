-- Non-idempotent: will fail on second run with "index already exists"
CREATE INDEX idx_users_email ON users(email);
