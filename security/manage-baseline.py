#!/usr/bin/env python3
"""Security baseline management CLI for edictum-console.

Tracks findings from security audits, marks fixes, accepts risks,
detects regressions, and generates reports.

Usage:
    python security/manage-baseline.py status
    python security/manage-baseline.py status --severity critical
    python security/manage-baseline.py fix C1-approval-race --commit abc123
    python security/manage-baseline.py accept L1-api-hardening --reason "Low risk"
    python security/manage-baseline.py regressions
    python security/manage-baseline.py add NEW-FINDING --severity medium --issue 29 \\
        --file "src/foo.py" --description "Description here"
    python security/manage-baseline.py report
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

BASELINE_PATH = Path(__file__).resolve().parent / "baseline.json"

SEVERITY_ORDER: dict[str, int] = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
}

# ANSI color codes for terminal output
COLORS: dict[str, str] = {
    "critical": "\033[91m",  # bright red
    "high": "\033[31m",      # red
    "medium": "\033[33m",    # yellow
    "low": "\033[36m",       # cyan
    "fixed": "\033[32m",     # green
    "accepted": "\033[90m",  # gray
    "reset": "\033[0m",
    "bold": "\033[1m",
}


def load_baseline() -> dict:
    """Load the baseline JSON file."""
    if not BASELINE_PATH.exists():
        print(f"Error: baseline file not found at {BASELINE_PATH}", file=sys.stderr)
        sys.exit(1)
    with open(BASELINE_PATH) as f:
        return json.load(f)


def save_baseline(data: dict) -> None:
    """Write the baseline JSON file with pretty-printing."""
    with open(BASELINE_PATH, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def colorize(text: str, color: str) -> str:
    """Wrap text in ANSI color codes."""
    if not sys.stdout.isatty():
        return text
    return f"{COLORS.get(color, '')}{text}{COLORS['reset']}"


def severity_sort_key(item: tuple[str, dict]) -> tuple[int, str]:
    """Sort key: severity order (critical first), then finding ID."""
    _, finding = item
    return (SEVERITY_ORDER.get(finding["severity"], 99), item[0])


def format_table_row(finding_id: str, finding: dict) -> str:
    """Format a single finding as a table row."""
    severity = finding["severity"]
    status = finding["status"]
    issue = f"#{finding['issue']}" if "issue" in finding else "-"
    desc = finding.get("description", "")
    if len(desc) > 70:
        desc = desc[:67] + "..."

    colored_severity = colorize(severity.upper(), severity)
    colored_status = colorize(status, "fixed" if status == "fixed" else
                              "accepted" if status == "accepted" else severity)

    return (
        f"  {finding_id:<25s} {colored_severity:<22s} "
        f"{colored_status:<22s} {issue:<8s} {desc}"
    )


def cmd_status(args: argparse.Namespace) -> None:
    """Show all open (non-fixed, non-accepted) findings."""
    data = load_baseline()
    findings = data["findings"]

    # Filter by status: show fix-planned (open) by default
    open_findings = {
        fid: f for fid, f in findings.items()
        if f["status"] not in ("fixed", "accepted")
    }

    # Filter by severity if requested
    if args.severity:
        sev = args.severity.lower()
        open_findings = {
            fid: f for fid, f in open_findings.items()
            if f["severity"] == sev
        }

    if not open_findings:
        filter_msg = f" (severity={args.severity})" if args.severity else ""
        print(f"No open findings{filter_msg}.")
        return

    sorted_findings = sorted(open_findings.items(), key=severity_sort_key)

    print()
    print(colorize("Security Baseline - Open Findings", "bold"))
    print(f"  Last audit: {data.get('last_full_audit', 'unknown')}  "
          f"App version: {data.get('app_version', 'unknown')}")
    print()
    print(f"  {'FINDING':<25s} {'SEVERITY':<14s} {'STATUS':<14s} "
          f"{'ISSUE':<8s} DESCRIPTION")
    print(f"  {'─' * 25} {'─' * 14} {'─' * 14} {'─' * 8} {'─' * 50}")

    for fid, finding in sorted_findings:
        print(format_table_row(fid, finding))

    print()

    # Summary counts
    by_severity: dict[str, int] = {}
    for f in open_findings.values():
        by_severity[f["severity"]] = by_severity.get(f["severity"], 0) + 1

    summary_parts = []
    for sev in ("critical", "high", "medium", "low"):
        count = by_severity.get(sev, 0)
        if count > 0:
            summary_parts.append(colorize(f"{count} {sev}", sev))

    print(f"  Total open: {len(open_findings)} ({', '.join(summary_parts)})")
    print()


def cmd_fix(args: argparse.Namespace) -> None:
    """Mark a finding as fixed."""
    data = load_baseline()
    finding_id = args.finding_id

    if finding_id not in data["findings"]:
        print(f"Error: finding '{finding_id}' not found in baseline.", file=sys.stderr)
        sys.exit(1)

    finding = data["findings"][finding_id]

    if finding["status"] == "fixed":
        print(f"Finding '{finding_id}' is already marked as fixed "
              f"(commit: {finding.get('fixed_in', 'unknown')}).")
        return

    finding["status"] = "fixed"
    finding["fixed_in"] = args.commit
    finding["fixed_date"] = date.today().isoformat()

    save_baseline(data)
    print(f"Marked {colorize(finding_id, 'fixed')} as fixed "
          f"(commit: {args.commit}).")


def cmd_accept(args: argparse.Namespace) -> None:
    """Accept a finding (risk acknowledged, won't fix now)."""
    data = load_baseline()
    finding_id = args.finding_id

    if finding_id not in data["findings"]:
        print(f"Error: finding '{finding_id}' not found in baseline.", file=sys.stderr)
        sys.exit(1)

    finding = data["findings"][finding_id]

    if finding["severity"] == "critical":
        print(colorize(
            f"WARNING: Accepting a CRITICAL finding ({finding_id}). "
            f"Are you sure? This should be rare.",
            "critical"
        ))

    finding["status"] = "accepted"
    finding["reason"] = args.reason
    finding["accepted_date"] = date.today().isoformat()

    save_baseline(data)
    print(f"Accepted {colorize(finding_id, 'accepted')} "
          f"(reason: {args.reason}).")


def cmd_regressions(args: argparse.Namespace) -> None:
    """Check for regressions in fixed findings.

    For each finding marked "fixed", checks whether the associated file
    has been modified since the fix commit. If it has, the finding may
    have regressed and should be re-audited.
    """
    data = load_baseline()

    fixed_findings = {
        fid: f for fid, f in data["findings"].items()
        if f["status"] == "fixed"
    }

    if not fixed_findings:
        print("No fixed findings to check for regressions.")
        return

    # Determine the project root (two levels up from this script)
    project_root = BASELINE_PATH.parent.parent

    regressions_found: list[tuple[str, dict, str]] = []

    for fid, finding in sorted(fixed_findings.items(), key=severity_sort_key):
        fix_commit = finding.get("fixed_in")
        target_file = finding.get("file", "")

        if not fix_commit or not target_file:
            regressions_found.append((
                fid, finding,
                "Missing fix commit or file path -- cannot verify"
            ))
            continue

        full_path = project_root / target_file

        if not full_path.exists():
            # File was deleted -- likely not a regression
            continue

        # Check if the file has been modified since the fix commit
        try:
            result = subprocess.run(
                ["git", "log", "--oneline", f"{fix_commit}..HEAD", "--", target_file],
                capture_output=True,
                text=True,
                cwd=str(project_root),
                timeout=10,
            )
            if result.returncode != 0:
                regressions_found.append((
                    fid, finding,
                    f"git error: {result.stderr.strip()}"
                ))
                continue

            commits_since_fix = result.stdout.strip()
            if commits_since_fix:
                commit_count = len(commits_since_fix.splitlines())
                regressions_found.append((
                    fid, finding,
                    f"{commit_count} commit(s) modified {target_file} since fix"
                ))

        except FileNotFoundError:
            print("Warning: git not found. Cannot check regressions.", file=sys.stderr)
            return
        except subprocess.TimeoutExpired:
            regressions_found.append((
                fid, finding,
                "git command timed out"
            ))

    if not regressions_found:
        print(colorize("No regressions detected.", "fixed"))
        print(f"  Checked {len(fixed_findings)} fixed finding(s).")
        return

    print()
    print(colorize("Potential Regressions Detected", "bold"))
    print()
    print(f"  {'FINDING':<25s} {'SEVERITY':<14s} {'FIXED IN':<12s} REASON")
    print(f"  {'─' * 25} {'─' * 14} {'─' * 12} {'─' * 50}")

    for fid, finding, reason in regressions_found:
        severity = colorize(finding["severity"].upper(), finding["severity"])
        commit = finding.get("fixed_in", "?")[:10]
        print(f"  {fid:<25s} {severity:<22s} {commit:<12s} {reason}")

    print()
    print(colorize(
        f"  {len(regressions_found)} potential regression(s) found. "
        f"Re-audit these findings.",
        "high"
    ))
    print()


def cmd_add(args: argparse.Namespace) -> None:
    """Add a new finding to the baseline."""
    data = load_baseline()
    finding_id = args.finding_id

    if finding_id in data["findings"]:
        print(f"Error: finding '{finding_id}' already exists.", file=sys.stderr)
        sys.exit(1)

    if args.severity not in SEVERITY_ORDER:
        print(
            f"Error: severity must be one of: "
            f"{', '.join(SEVERITY_ORDER.keys())}",
            file=sys.stderr,
        )
        sys.exit(1)

    finding: dict[str, str | int] = {
        "status": "fix-planned",
        "severity": args.severity,
        "file": args.file,
        "description": args.description,
        "found": date.today().isoformat(),
    }

    if args.issue is not None:
        finding["issue"] = args.issue

    data["findings"][finding_id] = finding

    save_baseline(data)
    print(f"Added finding {colorize(finding_id, args.severity)} "
          f"(severity: {args.severity}).")


def cmd_report(args: argparse.Namespace) -> None:
    """Export open findings as a markdown table."""
    data = load_baseline()

    open_findings = {
        fid: f for fid, f in data["findings"].items()
        if f["status"] not in ("fixed",)
    }

    sorted_findings = sorted(open_findings.items(), key=severity_sort_key)

    lines: list[str] = [
        f"# Security Baseline Report",
        f"",
        f"**Last full audit:** {data.get('last_full_audit', 'unknown')}  ",
        f"**App version:** {data.get('app_version', 'unknown')}  ",
        f"**Generated:** {date.today().isoformat()}",
        f"",
        f"## Open Findings ({len(open_findings)})",
        f"",
        f"| Finding | Severity | Status | Issue | File | Description |",
        f"|---------|----------|--------|-------|------|-------------|",
    ]

    for fid, finding in sorted_findings:
        severity = finding["severity"]
        status = finding["status"]
        issue_num = finding.get("issue")
        issue = f"#{issue_num}" if issue_num else "-"
        file_path = finding.get("file", "-")
        desc = finding.get("description", "")
        lines.append(
            f"| {fid} | {severity} | {status} | {issue} | `{file_path}` | {desc} |"
        )

    # Fixed findings summary
    fixed_findings = {
        fid: f for fid, f in data["findings"].items()
        if f["status"] == "fixed"
    }
    if fixed_findings:
        lines.extend([
            f"",
            f"## Fixed Findings ({len(fixed_findings)})",
            f"",
            f"| Finding | Severity | Fixed In | Fixed Date |",
            f"|---------|----------|----------|------------|",
        ])
        for fid, finding in sorted(fixed_findings.items(), key=severity_sort_key):
            severity = finding["severity"]
            commit = finding.get("fixed_in", "-")
            fixed_date = finding.get("fixed_date", "-")
            lines.append(
                f"| {fid} | {severity} | `{commit}` | {fixed_date} |"
            )

    output = "\n".join(lines) + "\n"
    print(output)


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser with subcommands."""
    parser = argparse.ArgumentParser(
        prog="manage-baseline",
        description="Manage the edictum-console security baseline.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # status
    status_parser = subparsers.add_parser(
        "status", help="Show all open findings"
    )
    status_parser.add_argument(
        "--severity",
        choices=["critical", "high", "medium", "low"],
        help="Filter by severity level",
    )
    status_parser.set_defaults(func=cmd_status)

    # fix
    fix_parser = subparsers.add_parser(
        "fix", help="Mark a finding as fixed"
    )
    fix_parser.add_argument("finding_id", help="Finding ID (e.g. C1-approval-race)")
    fix_parser.add_argument(
        "--commit", required=True,
        help="Git commit hash that contains the fix",
    )
    fix_parser.set_defaults(func=cmd_fix)

    # accept
    accept_parser = subparsers.add_parser(
        "accept", help="Accept a finding (risk acknowledged)"
    )
    accept_parser.add_argument("finding_id", help="Finding ID")
    accept_parser.add_argument(
        "--reason", required=True,
        help="Reason for accepting the risk",
    )
    accept_parser.set_defaults(func=cmd_accept)

    # regressions
    regressions_parser = subparsers.add_parser(
        "regressions", help="Check for regressions in fixed findings"
    )
    regressions_parser.set_defaults(func=cmd_regressions)

    # add
    add_parser = subparsers.add_parser(
        "add", help="Add a new finding"
    )
    add_parser.add_argument("finding_id", help="Finding ID (e.g. H3-new-issue)")
    add_parser.add_argument(
        "--severity", required=True,
        choices=["critical", "high", "medium", "low"],
        help="Severity level",
    )
    add_parser.add_argument(
        "--issue", type=int, default=None,
        help="GitHub issue number",
    )
    add_parser.add_argument(
        "--file", required=True,
        help="Primary file affected",
    )
    add_parser.add_argument(
        "--description", required=True,
        help="Short description of the finding",
    )
    add_parser.set_defaults(func=cmd_add)

    # report
    report_parser = subparsers.add_parser(
        "report", help="Export open findings as markdown"
    )
    report_parser.set_defaults(func=cmd_report)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
