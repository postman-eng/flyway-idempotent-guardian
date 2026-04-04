# flyway-idempotent-guardian

A GitHub Action that automatically detects non-idempotent SQL in Flyway migration pull requests and either suggests or directly opens a fixing PR with properly guarded DDL.

## The Problem

Flyway versioned migrations (`V1__create_users.sql`) are executed once and checksummed. But teams that use Flyway in environments where migrations may be re-applied — disaster recovery, test environments, schema drift repair, or repeatable pipelines — need every migration to be safe to run more than once.

A bare `ALTER TABLE users ADD COLUMN phone VARCHAR(20);` will fail with `column "phone" already exists` the second time it runs. The correct pattern wraps the statement in an existence check, but this is easy to forget and tedious to write correctly for every DDL variant.

This action catches that at PR time — before it reaches any database.

---

## What It Does

On every pull request that touches a Flyway migration file:

1. **Detects** which SQL statements are not idempotent
2. **Posts a PR comment** showing the original SQL alongside the corrected version
3. **Opens a fixing PR** (optional) targeting the same base branch with the wrapped SQL applied

The action is advisory by default — it exits 0 and does not block the original PR. Engineers can merge the fix PR directly or apply the changes manually.

---

## Supported DDL Operations

| Operation                                | PostgreSQL                                            | MySQL                                              |
| ---------------------------------------- | ----------------------------------------------------- | -------------------------------------------------- |
| `CREATE TABLE`                           | `DO $$ IF NOT EXISTS (pg_tables) $$`                  | `CREATE TABLE IF NOT EXISTS`                       |
| `ALTER TABLE ADD COLUMN`                 | `DO $$ IF NOT EXISTS (information_schema.columns) $$` | `PREPARE` + `information_schema.columns`           |
| `ALTER TABLE ALTER COLUMN TYPE`          | `DO $$ IF EXISTS (column) $$`                         | `PREPARE` + `information_schema.columns`           |
| `CREATE INDEX`                           | `DO $$ IF NOT EXISTS (pg_indexes) $$`                 | `PREPARE` + `information_schema.statistics`        |
| `CREATE UNIQUE INDEX`                    | `DO $$ IF NOT EXISTS (pg_indexes) $$`                 | `PREPARE` + `information_schema.statistics`        |
| `ALTER TABLE ADD CONSTRAINT FOREIGN KEY` | `DO $$ IF NOT EXISTS (table_constraints) $$`          | `PREPARE` + `information_schema.table_constraints` |
| `ALTER TABLE ADD CONSTRAINT UNIQUE`      | `DO $$ IF NOT EXISTS (table_constraints) $$`          | `PREPARE` + `information_schema.table_constraints` |
| `ALTER TABLE ADD CONSTRAINT CHECK`       | `DO $$ IF NOT EXISTS (table_constraints) $$`          | `PREPARE` + `information_schema.table_constraints` |
| `ALTER TABLE DROP COLUMN`                | `DO $$ IF EXISTS (column) $$`                         | `PREPARE` + `information_schema.columns`           |
| `DROP TABLE`                             | `DROP TABLE IF EXISTS`                                | `DROP TABLE IF EXISTS`                             |
| `CREATE TYPE`                            | `DO $$ IF NOT EXISTS (pg_type) $$`                    | —                                                  |
| `ALTER TABLE ALTER COLUMN SET NOT NULL`  | `DO $$ IF is_nullable = 'YES' $$`                     | `PREPARE` + `IS_NULLABLE` check                    |
| `ALTER TABLE RENAME COLUMN`              | `DO $$ IF old exists AND new does not $$`             | `PREPARE` + dual column check                      |
| `ALTER TABLE DROP CONSTRAINT`            | `DO $$ IF EXISTS (table_constraints) $$`              | `PREPARE` + `information_schema.table_constraints` |
| `CREATE VIEW`                            | Rewritten to `CREATE OR REPLACE VIEW`                 | `CREATE OR REPLACE VIEW`                           |
| `CREATE FUNCTION`                        | Rewritten to `CREATE OR REPLACE FUNCTION`             | —                                                  |

**Already-idempotent SQL is passed through unchanged.** The action recognises:

- `DO $$` blocks (PostgreSQL)
- `IF NOT EXISTS` / `IF EXISTS` clauses
- `CREATE OR REPLACE` statements
- MySQL `PREPARE stmt` patterns

---

## Quick Start

### 1. Add the workflow to your Flyway migration repo

Create `.github/workflows/flyway-guardian.yml`:

```yaml
name: Flyway Idempotent Guardian

on:
  pull_request:
    paths:
      - "migrations/**/*.sql"

jobs:
  guardian:
    name: Check SQL Idempotency
    runs-on: ubuntu-latest
    permissions:
      contents: write # create fix branch and commit
      pull-requests: write # post comments and open the fixing PR

    steps:
      - uses: actions/checkout@v4

      - name: Run flyway-idempotent-guardian
        uses: postman-eng/flyway-idempotent-guardian@v1
        with:
          github-token: ${{ secrets.GITHUB_TOKEN }}
          dialect: auto
          migration-path: "migrations/**/*.sql"
          auto-pr: "true"
```

### 2. Open a PR with a migration file

Create `migrations/V4__add_status_column.sql`:

```sql
ALTER TABLE users ADD COLUMN status VARCHAR(20);
```

The action will post a comment like:

> **flyway-idempotent-guardian**
>
> `migrations/V4__add_status_column.sql` contains SQL that is **not idempotent** and may fail on re-run or rollback.
>
> **Suggested idempotent replacement:**
>
> ```sql
> DO $$
> BEGIN
>     IF NOT EXISTS (
>         SELECT FROM information_schema.columns
>         WHERE table_schema = 'public'
>         AND table_name = 'users'
>         AND column_name = 'status'
>     ) THEN
>         ALTER TABLE users ADD COLUMN status VARCHAR(20);
>     END IF;
> END $$;
> ```

And open a PR `fix/idempotent-4-abc1234` with that change applied.

---

## Configuration

### Inputs

| Input            | Required | Default        | Description                                                  |
| ---------------- | -------- | -------------- | ------------------------------------------------------------ |
| `github-token`   | Yes      | —              | GitHub token for API access. Use `secrets.GITHUB_TOKEN`.     |
| `dialect`        | No       | `auto`         | Database dialect: `postgres`, `mysql`, or `auto`.            |
| `migration-path` | No       | `**/V*__*.sql` | Glob pattern for Flyway migration files.                     |
| `auto-pr`        | No       | `true`         | Whether to open a fixing PR. Set to `false` to comment only. |

### `dialect: auto` detection

When `dialect` is set to `auto`, the action resolves the dialect in this order:

1. A `-- dialect: postgres` or `-- dialect: mysql` comment at the top of the file
2. SQL syntax heuristics:
   - `SERIAL`, `DO $$`, or `LANGUAGE plpgsql` → PostgreSQL
   - `AUTO_INCREMENT` or `ENGINE =` → MySQL
3. Default: PostgreSQL

To force a specific dialect per file, add a comment at the top:

```sql
-- dialect: mysql
ALTER TABLE users ADD COLUMN phone VARCHAR(20);
```

### `auto-pr: false` — comment-only mode

If you want the action to suggest fixes without opening additional PRs:

```yaml
- uses: postman-eng/flyway-idempotent-guardian@v1
  with:
    github-token: ${{ secrets.GITHUB_TOKEN }}
    auto-pr: "false"
```

The action will still post a comment with the corrected SQL. Engineers apply it manually.

### Blocking the PR (optional)

By default the action exits 0 and is purely advisory. To make it a required status check that blocks non-idempotent migrations from merging, wrap it:

```yaml
- name: Run flyway-idempotent-guardian
  id: guardian
  uses: postman-eng/flyway-idempotent-guardian@v1
  with:
    github-token: ${{ secrets.GITHUB_TOKEN }}

- name: Fail if non-idempotent SQL detected
  if: steps.guardian.outputs.violations > 0
  run: exit 1
```

> Note: the `violations` output is planned for a future release. Currently, use the advisory comment + fix PR workflow.

---

## How the Fix PR Works

When `auto-pr: true` (the default), for each non-idempotent file the action:

1. Creates branch `fix/idempotent-{pr_number}-{short_sha}` from the PR head commit
2. Commits the wrapped SQL to that branch with message:
   ```
   fix: wrap migrations/V4__add_status_column.sql with idempotency guards
   ```
3. Opens a PR targeting the **same base branch** as the original PR
4. Links back to the original PR in the fix PR body

The fix PR can be:

- **Merged directly** — the original PR then needs a rebase to pick it up
- **Cherry-picked** — apply the commit to the original branch manually
- **Used as reference** — copy the SQL from the fix PR and update the original branch

---

## Pattern Reference

### PostgreSQL

PostgreSQL idempotency uses `DO $$` anonymous blocks that query system catalogs.

**CREATE TABLE**

```sql
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT FROM pg_tables
        WHERE schemaname = 'public'
        AND tablename = 'users'
    ) THEN
        CREATE TABLE users (
            id SERIAL PRIMARY KEY,
            name VARCHAR(255) NOT NULL
        );
    END IF;
END $$;
```

**ADD COLUMN**

```sql
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT FROM information_schema.columns
        WHERE table_schema = 'public'
        AND table_name = 'users'
        AND column_name = 'phone'
    ) THEN
        ALTER TABLE users ADD COLUMN phone VARCHAR(20);
    END IF;
END $$;
```

**CREATE INDEX / CREATE UNIQUE INDEX**

```sql
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT FROM pg_indexes
        WHERE schemaname = 'public'
        AND tablename = 'users'
        AND indexname = 'idx_users_email'
    ) THEN
        CREATE INDEX idx_users_email ON users(email);
    END IF;
END $$;
```

**ADD FOREIGN KEY**

```sql
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT FROM information_schema.table_constraints
        WHERE constraint_schema = 'public'
        AND table_name = 'orders'
        AND constraint_name = 'fk_orders_user_id'
        AND constraint_type = 'FOREIGN KEY'
    ) THEN
        ALTER TABLE orders
        ADD CONSTRAINT fk_orders_user_id
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
    END IF;
END $$;
```

**DROP COLUMN**

```sql
DO $$
BEGIN
    IF EXISTS (
        SELECT FROM information_schema.columns
        WHERE table_schema = 'public'
        AND table_name = 'users'
        AND column_name = 'obsolete_field'
    ) THEN
        ALTER TABLE users DROP COLUMN obsolete_field;
    END IF;
END $$;
```

**DROP TABLE**

```sql
DROP TABLE IF EXISTS obsolete_table CASCADE;
```

**CREATE TYPE (ENUM)**

```sql
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'user_status') THEN
        CREATE TYPE user_status AS ENUM ('active', 'inactive', 'suspended');
    END IF;
END $$;
```

**SET NOT NULL**

```sql
DO $$
BEGIN
    IF EXISTS (
        SELECT FROM information_schema.columns
        WHERE table_schema = 'public'
        AND table_name = 'users'
        AND column_name = 'email'
        AND is_nullable = 'YES'
    ) THEN
        UPDATE public.users SET email = DEFAULT WHERE email IS NULL;
        ALTER TABLE users ALTER COLUMN email SET NOT NULL;
    END IF;
END $$;
```

**RENAME COLUMN**

```sql
DO $$
BEGIN
    IF EXISTS (
        SELECT FROM information_schema.columns
        WHERE table_schema = 'public'
        AND table_name = 'users'
        AND column_name = 'old_name'
    ) AND NOT EXISTS (
        SELECT FROM information_schema.columns
        WHERE table_schema = 'public'
        AND table_name = 'users'
        AND column_name = 'new_name'
    ) THEN
        ALTER TABLE users RENAME COLUMN old_name TO new_name;
    END IF;
END $$;
```

**CREATE VIEW / CREATE FUNCTION**

These are rewritten inline — no block needed:

```sql
-- Input:
CREATE VIEW v_active_users AS SELECT id, name FROM users WHERE deleted_at IS NULL;

-- Output:
CREATE OR REPLACE VIEW v_active_users AS SELECT id, name FROM users WHERE deleted_at IS NULL;
```

---

### MySQL

MySQL lacks anonymous block support (pre-8.0), so idempotency uses session variables and prepared statements.

**CREATE TABLE**

```sql
CREATE TABLE IF NOT EXISTS users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(255) NOT NULL
);
```

**ADD COLUMN**

```sql
SET @col_exists = (
    SELECT COUNT(*)
    FROM information_schema.columns
    WHERE table_schema = DATABASE()
    AND table_name = 'users'
    AND column_name = 'phone'
);

SET @query = IF(@col_exists = 0,
    'ALTER TABLE users ADD COLUMN phone VARCHAR(20)',
    'SELECT "Column phone already exists" AS msg');

PREPARE stmt FROM @query;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
```

**CREATE INDEX**

```sql
SET @index_exists = (
    SELECT COUNT(*)
    FROM information_schema.statistics
    WHERE table_schema = DATABASE()
    AND table_name = 'users'
    AND index_name = 'idx_users_email'
);

SET @query = IF(@index_exists = 0,
    'CREATE INDEX idx_users_email ON users(email)',
    'SELECT "Index idx_users_email already exists" AS msg');

PREPARE stmt FROM @query;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
```

**DROP TABLE**

```sql
DROP TABLE IF EXISTS obsolete_table;
```

---

## Project Structure

```
flyway-idempotent-guardian/
├── action.yml              # GitHub Action metadata and input definitions
├── Dockerfile              # python:3.12-slim image used by the action
├── requirements.txt        # PyGithub, Jinja2, sqlparse, pytest
├── src/
│   ├── main.py             # Entry point — reads GitHub event, orchestrates pipeline
│   ├── sql_detector.py     # Regex-based DDL classification and idempotency detection
│   ├── wrapper.py          # Jinja2 template dispatch and rendering
│   └── gh_client.py        # GitHub API: post comments, create branches, open PRs
├── templates/
│   ├── postgres/           # 11 Jinja2 templates for PostgreSQL DDL patterns
│   └── mysql/              # 10 Jinja2 templates for MySQL DDL patterns
├── tests/
│   ├── test_sql_detector.py   # 35 tests: dialect detection, DDL typing, entity extraction
│   ├── test_wrapper.py        # 22 tests: template rendering for all DDL × dialect combos
│   └── fixtures/
│       ├── postgres/non_idempotent/   # Sample bare DDL input files
│       └── mysql/non_idempotent/
└── demo/
    ├── .github/workflows/flyway-guardian.yml   # Example consumer workflow
    └── migrations/                             # Non-idempotent example migrations
        ├── V1__create_users.sql
        ├── V2__add_phone_column.sql
        └── V3__create_idx_email.sql
```

---

## Development

### Prerequisites

- Python 3.11+
- pip

### Setup

```bash
git clone https://github.com/postman-eng/flyway-idempotent-guardian
cd flyway-idempotent-guardian
pip install -r requirements.txt
```

### Running Tests

```bash
pytest tests/ -v
```

All 57 tests should pass. The test suite covers:

- Dialect detection (explicit, file comment, heuristic, default)
- DDL type classification for all 16 operation types
- Idempotency marker detection (DO blocks, IF NOT EXISTS, CREATE OR REPLACE, PREPARE)
- Named entity extraction (table, column, index, constraint, schema)
- Template rendering for PostgreSQL and MySQL
- Edge cases: unknown DDL, already-idempotent passthrough

### Testing the Action Locally

You can run `main.py` locally against a synthetic GitHub event payload:

```bash
# Create a synthetic event payload
cat > /tmp/event.json << 'EOF'
{
  "pull_request": {
    "number": 42,
    "head": { "sha": "abc1234", "ref": "feature/add-phone" },
    "base": { "ref": "main" }
  },
  "repository": {
    "full_name": "your-org/your-flyway-repo"
  }
}
EOF

GITHUB_TOKEN=ghp_yourtoken \
GITHUB_EVENT_PATH=/tmp/event.json \
INPUT_DIALECT=auto \
INPUT_MIGRATION_PATH="**/V*__*.sql" \
INPUT_AUTO_PR=false \
python3 src/main.py
```

### Adding a New DDL Pattern

1. Add the regex rule to `_RULES` in `src/sql_detector.py` with named group mappings
2. Add the `DdlType` enum value
3. Map the new type to a template filename in `wrapper.py`
4. Create `templates/postgres/<type>.sql.j2` and/or `templates/mysql/<type>.sql.j2`
5. Add test cases to `tests/test_sql_detector.py` and `tests/test_wrapper.py`

### Adding a New Dialect

1. Create a `templates/<dialect>/` directory with templates matching the existing filenames
2. Update `detect_dialect()` in `sql_detector.py` with heuristics for the new dialect
3. Update the `dialect` input description in `action.yml`

---

## Flyway-Specific Notes

### Versioned vs Repeatable Migrations

Flyway has two migration types:

| Type       | Naming                | Checksum               | Re-run          |
| ---------- | --------------------- | ---------------------- | --------------- |
| Versioned  | `V1__description.sql` | Validated on every run | Never re-run    |
| Repeatable | `R__description.sql`  | Re-run when changed    | On every change |

This action targets **versioned migrations** (`V*__*.sql`) by default. Repeatable migrations (`R__*.sql`) should already be idempotent by design (views, functions, stored procedures using `CREATE OR REPLACE`).

To include repeatable migrations in the scan:

```yaml
migration-path: "migrations/**/*.sql" # catches both V and R prefixes
```

### Flyway Checksums and Modifying Migrations

Flyway validates the checksum of every previously-applied versioned migration on each run. **Do not modify a migration file after it has been applied to any environment.** The fix PR this action creates targets the PR branch, not any already-applied migration.

If a migration has already been applied and needs to be made idempotent retrospectively, the correct approach is:

1. Apply the fix PR to future environments only
2. Use `flyway repair` to update the checksum in the schema history table if needed
3. Or add a new migration that re-applies the operation safely

### Schema Qualification

The action defaults to `schemaname = 'public'` for PostgreSQL checks. If your tables live in a custom schema, qualify the table name in your migration:

```sql
ALTER TABLE myschema.users ADD COLUMN phone VARCHAR(20);
```

The action will extract `myschema` from the statement and use it in the generated existence check.

---

## Limitations

- **Single-statement files**: The action processes the entire file as one logical statement. Files containing multiple DDL statements separated by semicolons will be classified by the first detected DDL type. For multi-statement migrations, split into separate files (which is Flyway best practice anyway).
- **Complex expressions**: Regex-based detection cannot parse arbitrarily complex SQL. Statements with unusual whitespace, inline comments between keywords, or dialect extensions not covered by the patterns will be classified as `UNKNOWN` and flagged for manual review.
- **MySQL 8.0 features**: `RENAME COLUMN` requires MySQL 8.0+. `CHECK` constraints require MySQL 8.0.16+. The generated wrappers note these requirements in comments.
- **No dry-run output**: The action requires a live GitHub token to post comments and create PRs. For local testing, use `auto-pr: false` and redirect output.

---

## Security

The action uses `secrets.GITHUB_TOKEN` scoped to the repository. It requires:

- `contents: write` — to create the fix branch and commit
- `pull-requests: write` — to post comments and open the fix PR

No database connection is made. The action operates entirely on the SQL text in the migration file.

---

## License

MIT
