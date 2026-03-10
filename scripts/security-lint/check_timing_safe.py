#!/usr/bin/env python3
"""Security lint: detect timing-unsafe secret comparisons.

Scans Python files in src/edictum_server/ for == or != comparisons
involving variables whose names suggest they hold secrets (token, key,
secret, password, hash, etc.).  Timing-safe alternatives like
hmac.compare_digest must be used instead.

Usage (from repo root):
    python scripts/security-lint/check_timing_safe.py
    python scripts/security-lint/check_timing_safe.py --verbose

Exit codes:
    0  No violations found.
    1  One or more violations found.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Root of the source tree to scan (relative to repo root).
SRC_ROOT = Path("src/edictum_server")

# Identifiers containing any of these substrings are considered
# "secret-like" and must not appear in == / != comparisons.
SECRET_SUBSTRINGS: list[str] = [
    "secret",
    "token",
    "password",
    "hash",
    "api_key",
    "signing",
    "credential",
]

# Lines that contain any of these substrings are considered safe and
# skipped even if they match the regex.  Deliberately broad -- false
# positives from the linter are acceptable; false negatives are not.
SAFE_PATTERNS: list[str] = [
    "hmac.compare_digest",
    "bcrypt.checkpw",
    "is None",
    "is not None",
    ".startswith(",
    ".endswith(",
    "import ",
    "# ",
    '"""',
    "def ",
    "class ",
    "Field(",
    "Column(",
    "type=",
    "key=",
    "key:",
    "primary_key",
    "foreign_key",
    "cache_key",
    "config_key",
    "sort_key",
    "msg_key",
    "_key(",
    "Key(",
    "KeyPair",
    "signing_key_secret",
    "has_key",
    # Content hash comparisons (not secrets — comparing YAML/policy digests)
    "deployed_hash",
    "revision_hash",
    "policy_version",
    "content_hash",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_pattern() -> re.Pattern[str]:
    """Build a compiled regex that matches timing-unsafe comparisons.

    Matches either direction:
        <secret_var> == ...
        <secret_var> != ...
        ... == <secret_var>
        ... != <secret_var>

    An identifier is "secret-like" if it contains any of the substrings
    listed in SECRET_SUBSTRINGS.  Word boundaries (\\b) prevent matching
    inside unrelated words.
    """
    # Build an alternation of secret substrings.
    substr_alt = "|".join(re.escape(s) for s in SECRET_SUBSTRINGS)

    # An identifier that contains a secret substring.
    # Examples: session_token, api_key_hash, expected_secret
    secret_ident = rf"\b\w*(?:{substr_alt})\w*\b"

    # Left-hand side:  secret_var == ...  /  secret_var != ...
    lhs = rf"(?:{secret_ident})\s*(?:==|!=)"

    # Right-hand side: ... == secret_var  /  ... != secret_var
    rhs = rf"(?:==|!=)\s*(?:{secret_ident})"

    return re.compile(rf"({lhs}|{rhs})", re.IGNORECASE)


def _is_safe_line(line: str) -> bool:
    """Return True if the line is obviously safe and should be skipped."""
    stripped = line.lstrip()

    # Pure comment lines.
    if stripped.startswith("#"):
        return True

    # Lines containing a known-safe pattern.
    return any(pat in line for pat in SAFE_PATTERNS)


def _collect_python_files(root: Path) -> list[Path]:
    """Return sorted list of .py files under *root*, excluding tests."""
    return sorted(
        p
        for p in root.rglob("*.py")
        if "tests" not in p.parts and "__pycache__" not in p.parts
    )


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------


def scan_file(
    filepath: Path,
    pattern: re.Pattern[str],
    *,
    verbose: bool = False,
) -> list[str]:
    """Scan a single file and return a list of violation messages."""
    violations: list[str] = []

    try:
        text = filepath.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        if verbose:
            print(f"  SKIP: {filepath} ({exc})", file=sys.stderr)
        return violations

    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()

        # Skip empty lines.
        if not line:
            continue

        # Skip obviously-safe lines.
        if _is_safe_line(raw_line):
            continue

        # Check for timing-unsafe comparison.
        match = pattern.search(line)
        if match:
            truncated = line[:120] + ("..." if len(line) > 120 else "")
            violations.append(
                f"FAIL: {filepath}:{lineno} — "
                f"timing-unsafe comparison: {truncated}"
            )

    return violations


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Detect timing-unsafe secret comparisons in Python source."
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print per-file progress and skipped files.",
    )
    args = parser.parse_args(argv)

    if not SRC_ROOT.is_dir():
        print(
            f"ERROR: source root {SRC_ROOT} not found. "
            "Run this script from the repo root.",
            file=sys.stderr,
        )
        return 1

    pattern = _build_pattern()
    files = _collect_python_files(SRC_ROOT)
    all_violations: list[str] = []

    if args.verbose:
        print(f"Scanning {len(files)} Python files in {SRC_ROOT}/\n")

    for filepath in files:
        if args.verbose:
            print(f"  checking {filepath}")
        violations = scan_file(filepath, pattern, verbose=args.verbose)
        all_violations.extend(violations)

    # -- Output --------------------------------------------------------------
    if all_violations:
        print()
        for v in all_violations:
            print(v)
        print(
            f"\n{len(all_violations)} timing-unsafe comparison(s) found "
            f"across {len(files)} files scanned."
        )
        print(
            "Use hmac.compare_digest() for constant-time comparison "
            "of secrets."
        )
        return 1

    print(
        f"OK: {len(files)} files scanned, "
        "no timing-unsafe comparisons found."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
