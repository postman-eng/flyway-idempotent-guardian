-- Non-idempotent: will fail on second run with "column already exists"
ALTER TABLE users ADD COLUMN phone VARCHAR(20);
