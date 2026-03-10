#!/usr/bin/env python3
"""Check that all Pydantic request schemas have bounded str and list fields.

Unbounded string and list fields are a denial-of-service vector: an attacker
can send a 100 MB JSON body and the server will happily parse it into memory.
Every user-facing str/list field must declare ``max_length`` via
``Field(max_length=...)``.

Usage (from repo root):
    python scripts/security-lint/check_input_bounds.py
    python scripts/security-lint/check_input_bounds.py --verbose
"""

from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SCHEMAS_DIR = Path("src/edictum_server/schemas")

# Fields that legitimately skip max_length (JSON blobs, structured data
# validated elsewhere, or dict fields that aren't plain str/list).
ALLOWED_FIELDS: set[str] = {
    "tool_args",
    "payload",
    "config",
    "config_encrypted",
    "filters",
    "metadata",
    "definition",
    "claims",
    "tools_config",
    "observability",
    "tag_match",
    "tags",  # dict[str, Any] tags, not list[str]
}

# Only check classes whose name contains one of these substrings.
# Response / output schemas are not user input -- skip them.
INPUT_SUBSTRINGS: set[str] = {
    "Request",
    "Create",
    "Update",
    "Upload",
    "Import",
    "Upsert",
    "Batch",
    "Input",
}

# Class name suffixes that indicate response/output schemas.
# A class ending with any of these is skipped even if its name also
# contains an INPUT_SUBSTRINGS match (e.g. "CreateKeyResponse").
RESPONSE_SUFFIXES: tuple[str, ...] = (
    "Response",
    "Out",
    "Status",
    "Info",
    "Detail",
    "Summary",
    "Stats",
    "Result",
)

# Base class names we recognize as Pydantic models.
PYDANTIC_BASES: set[str] = {"BaseModel", "BaseSettings"}


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class Violation:
    filepath: Path
    line: int
    class_name: str
    field_name: str
    annotation: str
    reason: str

    def __str__(self) -> str:
        return (
            f"FAIL: {self.filepath}:{self.line} -- "
            f"{self.class_name}.{self.field_name}: "
            f"{self.annotation} {self.reason}"
        )


@dataclass
class CheckResult:
    violations: list[Violation] = field(default_factory=list)
    classes_checked: int = 0
    fields_checked: int = 0
    files_checked: int = 0


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------


def _inherits_pydantic(node: ast.ClassDef) -> bool:
    """Return True if the class inherits from BaseModel or similar."""
    for base in node.bases:
        if isinstance(base, ast.Name) and base.id in PYDANTIC_BASES:
            return True
        # Handle dotted names like pydantic.BaseModel (unlikely but safe).
        if isinstance(base, ast.Attribute) and base.attr in PYDANTIC_BASES:
            return True
    return False


def _is_input_schema(name: str) -> bool:
    """Return True if the class name looks like a request/input schema.

    A class is considered an input schema if its name contains one of the
    INPUT_SUBSTRINGS *and* does not end with a RESPONSE_SUFFIXES entry.
    This avoids false positives on names like ``CreateKeyResponse``.
    """
    if name.endswith(RESPONSE_SUFFIXES):
        return False
    return any(sub in name for sub in INPUT_SUBSTRINGS)


def _annotation_is_str(node: ast.expr) -> bool:
    """Check if the annotation resolves to str (plain, Optional, or union with None)."""
    # Plain `str`
    if isinstance(node, ast.Name) and node.id == "str":
        return True

    # str | None  (ast.BinOp with | operator)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _annotation_is_str(node.left) or _annotation_is_str(node.right)

    # Optional[str]  ->  ast.Subscript(value=Name('Optional'), slice=Name('str'))
    if isinstance(node, ast.Subscript):
        if isinstance(node.value, ast.Name) and node.value.id == "Optional":
            return _annotation_is_str(node.slice)

    # Annotated[str, ...]
    if isinstance(node, ast.Subscript):
        if isinstance(node.value, ast.Name) and node.value.id == "Annotated":
            if isinstance(node.slice, ast.Tuple) and node.slice.elts:
                return _annotation_is_str(node.slice.elts[0])

    return False


def _annotation_is_list(node: ast.expr) -> bool:
    """Check if the annotation resolves to list[X] (plain, Optional, or union with None)."""
    # list  or  list[X]
    if isinstance(node, ast.Name) and node.id == "list":
        return True
    if isinstance(node, ast.Subscript):
        if isinstance(node.value, ast.Name) and node.value.id == "list":
            return True

    # list[X] | None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _annotation_is_list(node.left) or _annotation_is_list(node.right)

    # Optional[list[X]]
    if isinstance(node, ast.Subscript):
        if isinstance(node.value, ast.Name) and node.value.id == "Optional":
            return _annotation_is_list(node.slice)

    return False


def _annotation_label(node: ast.expr) -> str:
    """Best-effort human-readable label for an annotation node."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Subscript):
        base = _annotation_label(node.value)
        inner = _annotation_label(node.slice)
        return f"{base}[{inner}]"
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        left = _annotation_label(node.left)
        right = _annotation_label(node.right)
        return f"{left} | {right}"
    if isinstance(node, ast.Constant):
        return repr(node.value)
    if isinstance(node, ast.Attribute):
        return f"{_annotation_label(node.value)}.{node.attr}"
    if isinstance(node, ast.Tuple):
        return ", ".join(_annotation_label(e) for e in node.elts)
    return ast.dump(node)


def _field_call_has_max_length(node: ast.expr) -> bool:
    """Return True if ``node`` is ``Field(..., max_length=N)``."""
    if not isinstance(node, ast.Call):
        return False

    # The callee must be ``Field`` (or pydantic.Field).
    callee = node.func
    if isinstance(callee, ast.Name) and callee.id == "Field":
        pass
    elif isinstance(callee, ast.Attribute) and callee.attr == "Field":
        pass
    else:
        return False

    for kw in node.keywords:
        if kw.arg == "max_length":
            return True

    return False


def _field_has_max_length(assign: ast.AnnAssign) -> bool:
    """Return True if max_length is declared as a default OR inside Annotated[...]."""
    # Check assign.value (e.g. name: str = Field(max_length=255))
    if assign.value is not None and _field_call_has_max_length(assign.value):
        return True
    # Check annotation for Annotated[str, Field(max_length=255)]
    ann = assign.annotation
    if not isinstance(ann, ast.Subscript):
        return False
    if not (isinstance(ann.value, ast.Name) and ann.value.id == "Annotated"):
        return False
    if not isinstance(ann.slice, ast.Tuple):
        return False
    return any(_field_call_has_max_length(elt) for elt in ann.slice.elts[1:])


# ---------------------------------------------------------------------------
# Core check logic
# ---------------------------------------------------------------------------


def _check_class(
    cls: ast.ClassDef,
    filepath: Path,
    verbose: bool,
) -> list[Violation]:
    """Check a single Pydantic class definition for unbounded fields."""
    violations: list[Violation] = []

    for stmt in cls.body:
        if not isinstance(stmt, ast.AnnAssign):
            continue
        if not isinstance(stmt.target, ast.Name):
            continue

        field_name = stmt.target.id
        annotation = stmt.annotation

        # Skip allowlisted fields.
        if field_name in ALLOWED_FIELDS:
            if verbose:
                print(f"  SKIP (allowlist): {cls.name}.{field_name}")
            continue

        needs_bound = False
        kind = ""

        if _annotation_is_str(annotation):
            needs_bound = True
            kind = "str"
        elif _annotation_is_list(annotation):
            needs_bound = True
            kind = "list"

        if not needs_bound:
            continue

        has_bound = _field_has_max_length(stmt)

        if verbose:
            status = "OK" if has_bound else "MISSING"
            print(f"  {status}: {cls.name}.{field_name} ({kind})")

        if not has_bound:
            violations.append(
                Violation(
                    filepath=filepath,
                    line=stmt.lineno,
                    class_name=cls.name,
                    field_name=field_name,
                    annotation=_annotation_label(annotation),
                    reason=f"without max_length",
                )
            )

    return violations


def check_file(filepath: Path, verbose: bool) -> CheckResult:
    """Parse a single Python file and check all input-schema classes."""
    result = CheckResult(files_checked=1)
    source = filepath.read_text(encoding="utf-8")

    try:
        tree = ast.parse(source, filename=str(filepath))
    except SyntaxError as exc:
        print(f"WARN: cannot parse {filepath}: {exc}", file=sys.stderr)
        return result

    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if not _inherits_pydantic(node):
            continue
        if not _is_input_schema(node.name):
            continue

        if verbose:
            print(f"\nChecking {filepath}:{node.lineno} {node.name}")

        result.classes_checked += 1
        class_violations = _check_class(node, filepath, verbose)
        result.fields_checked += sum(
            1
            for stmt in node.body
            if isinstance(stmt, ast.AnnAssign)
            and isinstance(stmt.target, ast.Name)
            and stmt.target.id not in ALLOWED_FIELDS
        )
        result.violations.extend(class_violations)

    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check Pydantic request schemas for unbounded str/list fields."
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Show each field check."
    )
    parser.add_argument(
        "--schemas-dir",
        type=Path,
        default=SCHEMAS_DIR,
        help=f"Path to schemas directory (default: {SCHEMAS_DIR}).",
    )
    args = parser.parse_args()

    schemas_dir: Path = args.schemas_dir
    if not schemas_dir.is_dir():
        print(f"ERROR: schemas directory not found: {schemas_dir}", file=sys.stderr)
        return 1

    py_files = sorted(schemas_dir.glob("*.py"))
    if not py_files:
        print(f"WARN: no .py files found in {schemas_dir}", file=sys.stderr)
        return 0

    totals = CheckResult()

    for filepath in py_files:
        file_result = check_file(filepath, verbose=args.verbose)
        totals.files_checked += file_result.files_checked
        totals.classes_checked += file_result.classes_checked
        totals.fields_checked += file_result.fields_checked
        totals.violations.extend(file_result.violations)

    # Print violations
    if totals.violations:
        print()
        for v in totals.violations:
            print(v)

    # Summary
    print()
    print(f"Files scanned:   {totals.files_checked}")
    print(f"Classes checked: {totals.classes_checked}")
    print(f"Fields checked:  {totals.fields_checked}")
    print(f"Violations:      {len(totals.violations)}")

    if totals.violations:
        print()
        print(
            "Fix: add Field(max_length=N) to each flagged field, "
            "or add the field name to ALLOWED_FIELDS if it's a "
            "structured blob validated elsewhere."
        )
        return 1

    print("\nAll input schemas have bounded str/list fields.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
