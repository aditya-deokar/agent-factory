"""Command execution evidence collector (tests, build, lint)."""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING

from ..store import EvidenceStore

if TYPE_CHECKING:
    from ...config.model import ChecksConfig


def collect_command_evidence(
    root: Path,
    store: EvidenceStore,
    checks_config: ChecksConfig | None = None,
) -> None:
    """Run configured checks (test, build, lint) and store outputs as evidence."""
    if not checks_config:
        return

    commands = {
        "test": (checks_config.test, "tests/stdout.txt"),
        "build": (checks_config.build, "build/stdout.txt"),
        "lint": (checks_config.lint, "lint/stdout.txt"),
    }

    for kind, (cmd, rel_path) in commands.items():
        if not cmd:
            continue

        start_time = time.time()
        try:
            res = subprocess.run(
                cmd,
                cwd=root,
                shell=True,
                capture_output=True,
                text=True,
                check=False,
            )
            duration = round(time.time() - start_time, 2)
            combined = f"{res.stdout}\n{res.stderr}".strip()

            store.record_text(
                kind=kind,
                rel_dest=rel_path,
                content=combined,
                summary=f"Output of `{cmd}` (exit {res.returncode}, {duration}s)",
                command=cmd,
                exit_code=res.returncode,
                duration=duration,
            )

            # Check if test generated a junit xml report in common locations
            if kind == "test":
                candidates = [
                    root / "junit.xml",
                    root / "test-results" / "junit.xml",
                    root / ".agent-factory" / "evidence" / "tests" / "junit.xml",
                ]
                for cand in candidates:
                    if cand.exists():
                        store.add_artifact(
                            kind="test",
                            source_path=cand,
                            summary="JUnit test results XML",
                            rel_dest="tests/junit.xml",
                        )
                        break
        except OSError:
            pass
