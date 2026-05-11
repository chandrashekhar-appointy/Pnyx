#!/usr/bin/env python3
"""
Generate a comprehensive Markdown test report from all suite results.

Reads:
  - test-reports/suite_results.json  (produced by run_all_tests.sh)
  - test-reports/<suite>/results.json (pytest-json-report or Playwright JSON)

Writes:
  - test-reports/TEST_REPORT.md

Usage:
  python scripts/generate_test_report.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = REPO_ROOT / "test-reports"
SUITE_RESULTS = REPORT_DIR / "suite_results.json"
OUTPUT = REPORT_DIR / "TEST_REPORT.md"


def _git_info() -> dict:
    """Get current git SHA and branch."""
    info = {"sha": "unknown", "branch": "unknown"}
    try:
        info["sha"] = (
            subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=str(REPO_ROOT),
                stderr=subprocess.DEVNULL,
            )
            .decode()
            .strip()
        )
        info["branch"] = (
            subprocess.check_output(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=str(REPO_ROOT),
                stderr=subprocess.DEVNULL,
            )
            .decode()
            .strip()
        )
    except Exception:
        pass
    return info


def _load_suite_results() -> list[dict]:
    """Load the aggregated suite results JSON."""
    if not SUITE_RESULTS.exists():
        return []
    try:
        return json.loads(SUITE_RESULTS.read_text())
    except (json.JSONDecodeError, IOError):
        return []


def _extract_failed_tests(suite: dict) -> list[str]:
    """Extract names of failed tests from detail files."""
    detail_file = suite.get("detail_file", "")
    if not detail_file or not Path(detail_file).exists():
        return []

    try:
        data = json.loads(Path(detail_file).read_text())
    except (json.JSONDecodeError, IOError):
        return []

    failed = []

    # pytest-json-report format
    if "tests" in data:
        for test in data["tests"]:
            if test.get("outcome") in ("failed", "error"):
                nodeid = test.get("nodeid", "unknown")
                message = ""
                if "call" in test and "longrepr" in test["call"]:
                    message = test["call"]["longrepr"][:200]
                elif "longrepr" in test:
                    message = str(test["longrepr"])[:200]
                failed.append(f"- `{nodeid}`" + (f"\n  > {message}" if message else ""))

    # Playwright JSON format
    if "suites" in data:
        def _walk_pw(suite_data: dict, prefix: str = ""):
            for spec in suite_data.get("specs", []):
                for test in spec.get("tests", []):
                    for result in test.get("results", []):
                        if result.get("status") in ("failed", "timedOut"):
                            title = spec.get("title", "unknown")
                            error = ""
                            if result.get("error", {}).get("message"):
                                error = result["error"]["message"][:200]
                            full_name = f"{prefix}{title}" if prefix else title
                            failed.append(
                                f"- `{full_name}`"
                                + (f"\n  > {error}" if error else "")
                            )
            for sub in suite_data.get("suites", []):
                sub_prefix = f"{prefix}{sub.get('title', '')} > "
                _walk_pw(sub, sub_prefix)

        for s in data["suites"]:
            _walk_pw(s)

    return failed


def _status_emoji(status: str) -> str:
    return "✅" if status == "passed" else "❌"


def _format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{minutes}m {secs}s"


def generate_report() -> str:
    """Generate the full Markdown report."""
    suites = _load_suite_results()
    git = _git_info()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    total_passed = sum(s.get("passed", 0) for s in suites)
    total_failed = sum(s.get("failed", 0) for s in suites)
    total_skipped = sum(s.get("skipped", 0) for s in suites)
    total_duration = sum(s.get("duration_seconds", 0) for s in suites)
    all_passed = all(s.get("status") == "passed" for s in suites) if suites else False

    overall_badge = "✅ ALL PASSED" if all_passed else "❌ FAILURES DETECTED"

    lines = []
    lines.append("# 🧪 Test Report — Meeting Co-Pilot")
    lines.append("")
    lines.append(f"**Generated**: {now}")
    lines.append(f"**Git**: `{git['branch']}` @ `{git['sha']}`")
    lines.append(f"**Overall**: **{overall_badge}**")
    lines.append("")

    # ── Summary Table ────────────────────────────────────────────────────
    lines.append("## Summary")
    lines.append("")
    lines.append("| Suite | Status | Passed | Failed | Skipped | Duration |")
    lines.append("|-------|--------|-------:|-------:|--------:|---------:|")

    for s in suites:
        emoji = _status_emoji(s.get("status", "unknown"))
        lines.append(
            f"| {s['suite']} | {emoji} {s.get('status', 'unknown')} "
            f"| {s.get('passed', 0)} "
            f"| {s.get('failed', 0)} "
            f"| {s.get('skipped', 0)} "
            f"| {_format_duration(s.get('duration_seconds', 0))} |"
        )

    lines.append(f"| **TOTAL** | **{overall_badge}** "
                 f"| **{total_passed}** "
                 f"| **{total_failed}** "
                 f"| **{total_skipped}** "
                 f"| **{_format_duration(total_duration)}** |")
    lines.append("")

    # ── Failed Tests Detail ──────────────────────────────────────────────
    has_failures = any(s.get("failed", 0) > 0 for s in suites)
    if has_failures:
        lines.append("## ❌ Failed Tests")
        lines.append("")
        for s in suites:
            if s.get("failed", 0) > 0:
                lines.append(f"### {s['suite']}")
                lines.append("")
                failed_tests = _extract_failed_tests(s)
                if failed_tests:
                    for ft in failed_tests[:20]:  # Cap at 20 per suite
                        lines.append(ft)
                else:
                    lines.append(
                        f"- {s.get('failed', 0)} test(s) failed "
                        "(details not available in report JSON)"
                    )
                lines.append("")

    # ── Suite Details ────────────────────────────────────────────────────
    lines.append("## Suite Details")
    lines.append("")

    for s in suites:
        emoji = _status_emoji(s.get("status", "unknown"))
        lines.append(f"### {emoji} {s['suite']}")
        lines.append("")
        lines.append(f"- **Status**: {s.get('status', 'unknown')}")
        lines.append(f"- **Duration**: {_format_duration(s.get('duration_seconds', 0))}")
        lines.append(f"- **Passed**: {s.get('passed', 0)}")
        lines.append(f"- **Failed**: {s.get('failed', 0)}")
        lines.append(f"- **Skipped**: {s.get('skipped', 0)}")
        if s.get("errors", 0) > 0:
            lines.append(f"- **Errors**: {s['errors']}")
        if s.get("detail_file"):
            lines.append(f"- **Detail**: `{s['detail_file']}`")
        lines.append("")

    # ── Test Categories Reference ────────────────────────────────────────
    lines.append("## Available Test Suites")
    lines.append("")
    lines.append("| Suite | Tool | Command |")
    lines.append("|-------|------|---------|")
    lines.append("| `unit` | pytest | `./scripts/run_all_tests.sh --suite unit` |")
    lines.append("| `integration` | pytest | `./scripts/run_all_tests.sh --suite integration` |")
    lines.append("| `backend` | pytest | `./scripts/run_all_tests.sh --suite backend` |")
    lines.append("| `security` | pytest | `./scripts/run_all_tests.sh --suite security` |")
    lines.append("| `chaos` | pytest | `./scripts/run_all_tests.sh --suite chaos` |")
    lines.append("| `contract` | pytest | `./scripts/run_all_tests.sh --suite contract` |")
    lines.append("| `migration` | pytest | `./scripts/run_all_tests.sh --suite migration` |")
    lines.append("| `llm_eval` | pytest | `LLM_EVAL_LIVE=1 ./scripts/run_all_tests.sh --suite llm_eval` |")
    lines.append("| `e2e` | Playwright | `./scripts/run_all_tests.sh --suite e2e` |")
    lines.append("| `a11y` | Playwright+axe | `./scripts/run_all_tests.sh --suite a11y` |")
    lines.append("| `visual` | Playwright | `./scripts/run_all_tests.sh --suite visual` |")
    lines.append("| `load` | k6 | `./scripts/run_all_tests.sh --suite load` |")
    lines.append("| `mutation` | mutmut | `./scripts/run_all_tests.sh --suite mutation` |")
    lines.append("| `synthetic` | Playwright | `./scripts/run_all_tests.sh --suite synthetic` |")
    lines.append("")

    lines.append("---")
    lines.append(f"*Report generated by `scripts/generate_test_report.py` at {now}*")
    lines.append("")

    return "\n".join(lines)


def main():
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report = generate_report()
    OUTPUT.write_text(report)
    print(f"✅ Report written to {OUTPUT}")


if __name__ == "__main__":
    main()
