"""
Entry point for the flyway-idempotent-guardian GitHub Action.

Reads the GitHub event payload, identifies changed migration files in the PR,
detects non-idempotent SQL, and posts a comment + opens a fixing PR.
"""
from __future__ import annotations

import json
import os
import sys

import gh_client
from sql_detector import detect, detect_dialect
from wrapper import wrap


def main() -> None:
    event_path = os.environ.get("GITHUB_EVENT_PATH", "")
    if not event_path:
        print("ERROR: GITHUB_EVENT_PATH not set", file=sys.stderr)
        sys.exit(1)

    with open(event_path) as f:
        event = json.load(f)

    pr_number = event["pull_request"]["number"]
    repo_full_name = event["repository"]["full_name"]

    dialect_hint = os.environ.get("INPUT_DIALECT", "auto")
    migration_path = os.environ.get("INPUT_MIGRATION_PATH", "**/V*__*.sql")
    auto_pr = os.environ.get("INPUT_AUTO_PR", "true").lower() == "true"

    print(f"Checking PR #{pr_number} in {repo_full_name}")

    repo, pr = gh_client.get_pr(repo_full_name, pr_number)
    changed_files = gh_client.get_changed_migration_files(repo, pr, migration_path)

    if not changed_files:
        print("No Flyway migration files changed — nothing to do.")
        return

    print(f"Found {len(changed_files)} migration file(s): {changed_files}")

    fixes: list[tuple[str, str, str]] = []  # (file_path, original_sql, fixed_sql)

    for file_path in changed_files:
        try:
            original_sql = gh_client.get_file_content(repo, pr, file_path)
        except Exception as e:
            print(f"WARN: Could not fetch {file_path}: {e}", file=sys.stderr)
            continue

        dialect = detect_dialect(original_sql, dialect_hint)
        result = detect(original_sql, dialect)

        print(f"  {file_path}: type={result.ddl_type.value}, already_idempotent={result.already_idempotent}, dialect={dialect}")

        if result.already_idempotent:
            print(f"  -> Already idempotent, skipping.")
            continue

        fixed_sql = wrap(result, pr_author=pr.user.login, pr_url=pr.html_url)
        if fixed_sql == original_sql:
            print(f"  -> Wrapper produced no change (unknown type), skipping.")
            continue

        fixes.append((file_path, original_sql, fixed_sql))

    if not fixes:
        print("All migration files are already idempotent.")
        return

    print(f"\n{len(fixes)} file(s) need idempotency fixes.")

    for file_path, original_sql, fixed_sql in fixes:
        gh_client.post_pr_comment(repo, pr, original_sql, fixed_sql, file_path)
        print(f"  Posted comment for {file_path}")

        if auto_pr:
            fix_pr_url = gh_client.open_fix_pr(repo, pr, file_path, fixed_sql)
            print(f"  Opened fixing PR: {fix_pr_url}")

    print("Done.")


if __name__ == "__main__":
    main()
