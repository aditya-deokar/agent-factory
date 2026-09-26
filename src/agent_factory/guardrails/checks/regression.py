"""Guardrail 8: Regression (spec §22).

Runs configured test/build/lint checks:
- Non-zero exit code -> fail.
- Parses JUnit XML or output for test counts; test count must not drop vs base baseline -> fail.
"""

from __future__ import annotations

import re
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any
import xml.etree.ElementTree as ET

from ...config.model import ChecksConfig
from ..model import CheckResult, Finding

if TYPE_CHECKING:
    from ...diff import DiffFragment

_TEST_COUNT_PATTERNS = [
    re.compile(r"(\d+)\s+passed", re.IGNORECASE),
    re.compile(r"tests:\s+(\d+)\s+passed", re.IGNORECASE),
    re.compile(r"(\d+)\s+tests? passed", re.IGNORECASE),
    re.compile(r"Tests:\s+(\d+)\s+passed,\s+(\d+)\s+total", re.IGNORECASE),
]


def parse_junit_test_count(junit_path: Path) -> int | None:
    if not junit_path.exists():
        return None
    try:
        tree = ET.parse(junit_path)
        root = tree.getroot()
        # <testsuite tests="N" failures="M" ...> or <testsuites tests="N" ...>
        tests_attr = root.attrib.get("tests")
        if tests_attr is not None:
            return int(tests_attr)
        # Sum suites
        total = 0
        for ts in root.findall(".//testsuite"):
            if "tests" in ts.attrib:
                total += int(ts.attrib["tests"])
        return total if total > 0 else None
    except Exception:
        return None


def extract_test_count_from_output(output: str) -> int | None:
    for pat in _TEST_COUNT_PATTERNS:
        m = pat.search(output)
        if m:
            try:
                return int(m.group(1))
            except (ValueError, IndexError):
                pass
    return None


def check_regression(
    root: Path,
    checks_config: ChecksConfig | None = None,
    test_baseline: int | None = None,
    runner: Callable[[str, Path], tuple[int, str]] | None = None,
) -> CheckResult:
    check_name = "regression"
    if not checks_config:
        return CheckResult(
            check=check_name,
            status="pass",
            summary="No regression checks configured in agent-factory.yaml.",
        )

    commands = {
        "lint": checks_config.lint,
        "build": checks_config.build,
        "test": checks_config.test,
    }

    configured = {k: v for k, v in commands.items() if v}
    if not configured:
        return CheckResult(
            check=check_name,
            status="pass",
            summary="No check commands configured (checks.test, checks.build, checks.lint).",
        )

    def default_run(cmd: str, cwd: Path) -> tuple[int, str]:
        res = subprocess.run(
            cmd,
            cwd=cwd,
            shell=True,
            capture_output=True,
            text=True,
            check=False,
        )
        combined = f"{res.stdout}\n{res.stderr}".strip()
        return res.returncode, combined

    run_cmd = runner or default_run
    findings: list[Finding] = []
    run_results: dict[str, Any] = {}
    observed_test_count: int | None = None

    for kind, cmd in configured.items():
        start_time = time.time()
        exit_code, output = run_cmd(cmd, root)
        duration = time.time() - start_time
        run_results[kind] = {
            "command": cmd,
            "exit_code": exit_code,
            "duration": round(duration, 2),
            "output_preview": output[:500],
        }

        if exit_code != 0:
            findings.append(
                Finding(
                    id=f"regress-{kind}-exit-{exit_code}",
                    check=check_name,
                    severity="fail",
                    message=f"Regression check '{kind}' failed (command: '{cmd}', exit code {exit_code}).",
                    fix_hint=f"Fix errors reported by '{cmd}' before completing the feature.",
                )
            )

        if kind == "test":
            # Try junit first if standard report exists
            junit_cand = root / ".agent-factory" / "evidence" / "tests" / "junit.xml"
            count = parse_junit_test_count(junit_cand) or extract_test_count_from_output(output)
            if count is not None:
                observed_test_count = count
                run_results["test_count"] = count
                if test_baseline is not None and count < test_baseline:
                    findings.append(
                        Finding(
                            id=f"regress-test-count-drop",
                            check=check_name,
                            severity="fail",
                            message=(
                                f"Test regression: test count dropped from baseline of {test_baseline} "
                                f"to {count}."
                            ),
                            fix_hint="Ensure all existing tests pass and no tests were deleted or skipped.",
                        )
                    )

    has_fail = any(f.severity == "fail" and not f.waived for f in findings)
    status = "fail" if has_fail else "pass"
    summary = (
        f"All configured checks passed ({', '.join(configured.keys())})."
        if status == "pass"
        else f"{len(findings)} regression check failure(s)."
    )

    return CheckResult(
        check=check_name,
        status=status,
        summary=summary,
        findings=findings,
        metadata={
            "runs": run_results,
            "test_count": observed_test_count,
            "test_baseline": test_baseline,
        },
    )
