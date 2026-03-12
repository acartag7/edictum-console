#!/usr/bin/env python3
"""Lint script for tenant isolation (S3 boundary).

Scans Python files in routes/ and services/ to find SQLAlchemy
select(), update(), and delete() calls that lack a tenant_id filter
in their .where() chain.

Usage:
    python scripts/security-lint/check_tenant_isolation.py
    python scripts/security-lint/check_tenant_isolation.py --verbose

Exit code 0 if no violations, 1 if any found.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Repo root -- script assumes it runs from the project root.
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCAN_DIRS = [
    REPO_ROOT / "src" / "edictum_server" / "routes",
    REPO_ROOT / "src" / "edictum_server" / "services",
]

# ---------------------------------------------------------------------------
# Allowlists -- files/functions that legitimately skip tenant_id filtering.
# ---------------------------------------------------------------------------

ALLOWED_FILES: set[str] = {
    "routes/health.py",  # System-wide health check, no auth
    "routes/auth.py",  # Auth endpoints, no tenant context yet
    "routes/setup.py",  # Bootstrap endpoint, S7 guarded
    "routes/slack.py",  # Webhook callback, tenant resolved from Redis
    "routes/telegram.py",  # Webhook callback, tenant resolved from Redis
    "routes/discord.py",  # Webhook callback, tenant resolved from Redis
}

ALLOWED_FUNCTIONS: set[str] = {
    # System-level functions that intentionally operate across all tenants.
    "expire_approvals",  # Background task: expires all pending approvals globally
    "find_user_by_email",  # Login: user lookup before tenant is known
    "get_user_count",  # Health/bootstrap check: system-wide, no auth
    "find_enabled_channels_by_type",  # Webhook: tenant resolved via signature verification
    "find_channel_by_id_and_type",  # Webhook: tenant resolved via signature verification
}

# The token we require somewhere in the same statement to prove tenant scoping.
TENANT_TOKEN = "tenant_id"

# ---------------------------------------------------------------------------
# Patterns we explicitly skip (NOT SQLAlchemy query operations).
# ---------------------------------------------------------------------------

# FastAPI route decorators: @router.delete(...), @router.get(...), etc.
_DECORATOR_RE = re.compile(r"^\s*@\w+\.(delete|get|post|put|patch)\s*\(")

# ORM-level delete on an already-fetched object: db.delete(obj)
_ORM_DELETE_RE = re.compile(r"(await\s+)?db\.delete\s*\(")

# .on_conflict_do_update() on an INSERT statement (not a standalone update)
_ON_CONFLICT_RE = re.compile(r"\.on_conflict_do_update\s*\(")

# Selecting from subquery columns: select(ranked.c.foo, ...)
# These operate on results of a subquery that was already tenant-filtered.
_SUBQUERY_SELECT_RE = re.compile(r"select\s*\(\s*\w+\.c\.")


def _is_allowed_file(rel_path: str) -> bool:
    """Check if a file is in the allowlist."""
    return any(rel_path.endswith(allowed) for allowed in ALLOWED_FILES)


def _find_enclosing_function(lines: list[str], line_idx: int) -> str | None:
    """Walk backwards from line_idx to find the nearest def/async def."""
    for i in range(line_idx, -1, -1):
        stripped = lines[i].lstrip()
        if stripped.startswith("def ") or stripped.startswith("async def "):
            parts = stripped.split("(", 1)
            name_part = parts[0]
            if name_part.startswith("async def "):
                return name_part[len("async def ") :]
            if name_part.startswith("def "):
                return name_part[len("def ") :]
    return None


def _count_parens(line: str) -> int:
    """Count net parenthesis/bracket/brace depth change in a line.

    Ignores characters inside string literals and comments.
    """
    depth = 0
    in_single_quote = False
    in_double_quote = False
    in_triple_single = False
    in_triple_double = False
    j = 0
    while j < len(line):
        if not in_single_quote and not in_double_quote:
            if line[j : j + 3] == '"""':
                in_triple_double = not in_triple_double
                j += 3
                continue
            if line[j : j + 3] == "'''":
                in_triple_single = not in_triple_single
                j += 3
                continue

        c = line[j]

        if in_triple_double or in_triple_single:
            j += 1
            continue

        if c == "\\" and (in_single_quote or in_double_quote):
            j += 2
            continue

        if c == '"' and not in_single_quote:
            in_double_quote = not in_double_quote
        elif c == "'" and not in_double_quote:
            in_single_quote = not in_single_quote
        elif not in_single_quote and not in_double_quote:
            if c == "#":
                break  # rest of line is comment
            if c in "([{":
                depth += 1
            elif c in ")]}":
                depth -= 1

        j += 1
    return depth


def _next_line_continues(lines: list[str], idx: int) -> bool:
    """Check if the next line is a dot-continuation of the current statement."""
    if idx + 1 >= len(lines):
        return False
    return lines[idx + 1].lstrip().startswith(".")


def _extract_statement(lines: list[str], start_idx: int) -> tuple[str, int]:
    """Extract a full multi-line statement starting at start_idx.

    Handles two common SQLAlchemy patterns:

    1. Wrapped in db.execute(...) with inner newlines:
        result = await db.execute(
            select(Bundle)
            .where(Bundle.tenant_id == tenant_id)
        )

    2. Method-chained across lines (each starting with a dot):
        stmt = select(Foo)
            .where(Foo.x == y)
            .order_by(Foo.z)

    The strategy: track cumulative paren depth AND check for dot-
    continuation lines. A statement ends when paren depth returns to
    zero (or below) AND the next line does not start with a dot.

    Returns (statement_text, end_line_index).
    """
    cumulative_depth = 0
    statement_lines: list[str] = []
    end_idx = start_idx

    for i in range(start_idx, len(lines)):
        line = lines[i]
        statement_lines.append(line)
        end_idx = i

        cumulative_depth += _count_parens(line)

        # A statement is complete when cumulative depth returns to 0 (or
        # below) AND the next line is not a dot-continuation.
        if cumulative_depth <= 0 and not _next_line_continues(lines, i):
            break

        # Even at depth 0, if the next line starts with a dot, the
        # chain continues -- keep going.
        if cumulative_depth == 0 and _next_line_continues(lines, i):
            continue

    return "\n".join(statement_lines), end_idx


def _find_comment_start(line: str) -> int:
    """Find the position of the first # not inside a string."""
    in_single = False
    in_double = False
    i = 0
    while i < len(line):
        c = line[i]
        if c == "\\" and (in_single or in_double):
            i += 2
            continue
        if c == '"' and not in_single:
            in_double = not in_double
        elif c == "'" and not in_double:
            in_single = not in_single
        elif c == "#" and not in_single and not in_double:
            return i
        i += 1
    return -1


def _is_sqlalchemy_operation(line: str) -> str | None:
    """Check if a line contains a SQLAlchemy query operation.

    Returns the operation name (e.g., "select") if found, None otherwise.
    Filters out false positives from decorators, ORM deletes, etc.
    """
    stripped = line.lstrip()

    # Skip comment-only lines
    if stripped.startswith("#"):
        return None

    # Skip import lines
    if stripped.startswith("import ") or stripped.startswith("from "):
        return None

    # Skip FastAPI route decorators: @router.delete("/path")
    if _DECORATOR_RE.match(line):
        return None

    # Skip ORM-level delete: await db.delete(obj)
    if _ORM_DELETE_RE.search(line):
        return None

    # Skip .on_conflict_do_update() -- part of an INSERT, not a standalone update
    if _ON_CONFLICT_RE.search(line):
        return None

    # Now check for actual SQLAlchemy operations
    for op_name in ("select", "update", "delete"):
        op_pattern = f"{op_name}("
        idx = line.find(op_pattern)
        if idx == -1:
            continue

        # Skip if inside a comment
        comment_idx = _find_comment_start(line)
        if comment_idx != -1 and comment_idx < idx:
            continue

        # Skip if inside a string literal (rough heuristic)
        prefix = line[:idx]
        if prefix.count('"') % 2 != 0 or prefix.count("'") % 2 != 0:
            continue

        # Skip if this is a method call on an object, not a bare function call.
        # e.g., "db.delete(" should have been caught above, but catch any
        # remaining "something.delete(" patterns (not SQLAlchemy Core).
        # SQLAlchemy Core: select(...), update(...), delete(...) -- bare calls
        # or "pg_insert(...).on_conflict..." patterns.
        # We want to catch: select(, update(, delete( as standalone calls.
        if idx > 0:
            char_before = line[idx - 1]
            # If preceded by a dot, it's a method call (e.g., db.delete),
            # not a SQLAlchemy Core operation, UNLESS it's something like
            # "await db.execute(\n    select(" on a new line.
            if char_before == ".":
                continue

        # Skip selects from subquery columns: select(ranked.c.foo, ...)
        # These query from an already tenant-filtered subquery result.
        remaining = line[idx:]
        if _SUBQUERY_SELECT_RE.match(remaining):
            continue

        # Skip scalar_subquery() / correlate() patterns -- these are
        # correlated subqueries used inside a tenant-filtered outer query.
        # The pattern: select(func.count(...)).where(...).correlate(...).scalar_subquery()
        # We check if the statement (just this line for now) contains .correlate(
        if ".correlate(" in line or ".scalar_subquery()" in line:
            continue

        return op_name

    return None


# Detects .where(*variable) splat -- tenant_id may be in the variable.
_WHERE_SPLAT_RE = re.compile(r"\.where\s*\(\s*\*\w+")


def _statement_has_tenant_id(statement: str) -> bool:
    """Check if a statement references tenant_id anywhere."""
    return TENANT_TOKEN in statement


def _get_function_body(lines: list[str], line_idx: int) -> str:
    """Extract the full body of the function enclosing line_idx.

    Used as a fallback when a statement uses *filters splat -- we check
    whether tenant_id appears in the function that builds the filters.
    """
    # Find the function start (def/async def)
    func_start = None
    for i in range(line_idx, -1, -1):
        stripped = lines[i].lstrip()
        if stripped.startswith("def ") or stripped.startswith("async def "):
            func_start = i
            break

    if func_start is None:
        return ""

    # Find the function end: next line at the same or lesser indentation
    # that starts a new def, class, or is a module-level statement.
    func_indent = len(lines[func_start]) - len(lines[func_start].lstrip())
    func_end = len(lines)
    for i in range(func_start + 1, len(lines)):
        stripped = lines[i].lstrip()
        if not stripped or stripped.startswith("#"):
            continue  # blank lines and comments
        line_indent = len(lines[i]) - len(lines[i].lstrip())
        if line_indent <= func_indent and stripped and not stripped.startswith("#"):
            func_end = i
            break

    return "\n".join(lines[func_start:func_end])


def scan_file(filepath: Path, verbose: bool = False) -> list[str]:
    """Scan a single Python file for tenant isolation violations.

    Returns a list of violation messages.
    """
    rel_path = str(filepath.relative_to(REPO_ROOT / "src" / "edictum_server"))

    if _is_allowed_file(rel_path):
        if verbose:
            print(f"  SKIP (allowlisted file): {rel_path}")
        return []

    try:
        source = filepath.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return [f"ERROR: {filepath}: could not read file: {exc}"]

    lines = source.splitlines()
    violations: list[str] = []
    i = 0
    visited_ranges: set[int] = set()

    while i < len(lines):
        line = lines[i]
        op_name = _is_sqlalchemy_operation(line)

        if op_name is not None and i not in visited_ranges:
            visited_ranges.add(i)

            # Check enclosing function against allowlist
            func_name = _find_enclosing_function(lines, i)
            if func_name and func_name in ALLOWED_FUNCTIONS:
                if verbose:
                    print(
                        f"  SKIP (allowlisted function): " f"{rel_path}:{i + 1} in {func_name}()"
                    )
                i += 1
                continue

            # Extract the full statement
            statement, end_idx = _extract_statement(lines, i)

            # Check for tenant_id in the statement itself
            if not _statement_has_tenant_id(statement):
                # Second chance: check if the statement is a correlated
                # subquery or selects from subquery columns (multi-line
                # patterns that the single-line check may have missed).
                if ".correlate(" in statement or ".scalar_subquery()" in statement:
                    if verbose:
                        print(f"  SKIP (correlated subquery): " f"{rel_path}:{i + 1}")
                    i = end_idx + 1
                    continue

                # Check for subquery column access in the full statement
                if _SUBQUERY_SELECT_RE.search(statement):
                    if verbose:
                        print(f"  SKIP (subquery column select): " f"{rel_path}:{i + 1}")
                    i = end_idx + 1
                    continue

                # Third chance: .where(*filters) splat pattern.
                # The tenant_id filter is built into a variable (e.g.,
                # ``filters = [Model.tenant_id == tenant_id]``) and
                # applied via ``*filters``. Check the enclosing function.
                if _WHERE_SPLAT_RE.search(statement):
                    func_body = _get_function_body(lines, i)
                    if TENANT_TOKEN in func_body:
                        if verbose:
                            print(f"  SKIP (tenant_id via *filters): " f"{rel_path}:{i + 1}")
                        i = end_idx + 1
                        continue

                line_num = i + 1
                msg = f"FAIL: {rel_path}:{line_num} " f"-- {op_name}() without tenant_id filter"
                if func_name:
                    msg += f" [in {func_name}()]"
                violations.append(msg)

                if verbose:
                    stmt_lines = statement.splitlines()[:5]
                    for sl in stmt_lines:
                        print(f"       | {sl}")
                    if len(statement.splitlines()) > 5:
                        remaining = len(statement.splitlines()) - 5
                        print(f"       | ... (+{remaining} more lines)")

            i = end_idx + 1
        else:
            i += 1

    return violations


def main() -> int:
    """Entry point. Returns 0 if clean, 1 if violations found."""
    parser = argparse.ArgumentParser(
        description="Check tenant isolation on SQLAlchemy queries (S3 boundary)."
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print skipped files/functions and offending statement snippets.",
    )
    args = parser.parse_args()

    all_violations: list[str] = []
    files_scanned = 0

    for scan_dir in SCAN_DIRS:
        if not scan_dir.is_dir():
            print(f"WARNING: directory not found: {scan_dir}", file=sys.stderr)
            continue

        for filepath in sorted(scan_dir.glob("*.py")):
            if filepath.name == "__init__.py":
                continue

            if args.verbose:
                print(f"Scanning: {filepath.relative_to(REPO_ROOT)}")

            files_scanned += 1
            violations = scan_file(filepath, verbose=args.verbose)
            all_violations.extend(violations)

    # Summary
    print()
    if all_violations:
        print("=" * 72)
        print(f"TENANT ISOLATION VIOLATIONS: {len(all_violations)}")
        print("=" * 72)
        for v in all_violations:
            print(f"  {v}")
        print()
        print(f"Scanned {files_scanned} files. " f"Found {len(all_violations)} violation(s).")
        print()
        print("To suppress false positives, add entries to ALLOWED_FILES or")
        print("ALLOWED_FUNCTIONS at the top of this script.")
        return 1
    else:
        print(f"OK: Scanned {files_scanned} files. " f"No tenant isolation violations found.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
