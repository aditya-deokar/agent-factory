"""Guardrail data models: findings, check results, and reports (spec §22)."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class CheckSeverity(str, Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


class Finding(BaseModel):
    id: str = Field(description="Unique finding identifier, e.g. dup-InvitationTokenService")
    check: str = Field(description="Check name, e.g. duplication, architecture")
    severity: str = Field(description="fail | warn")
    message: str
    path: str | None = None
    line: int | None = None
    rule_or_knowledge_uid: str | None = None
    fix_hint: str | None = None
    waived: bool = False
    waiver_reason: str | None = None

    @property
    def location(self) -> str:
        if not self.path:
            return ""
        return f"{self.path}:{self.line}" if self.line else self.path


class CheckResult(BaseModel):
    check: str
    status: str = Field(description="pass | warn | fail")
    summary: str
    findings: list[Finding] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def has_fails(self) -> bool:
        return any(f.severity == "fail" and not f.waived for f in self.findings)

    @property
    def has_warns(self) -> bool:
        return any(f.severity == "warn" and not f.waived for f in self.findings)


class GuardrailReport(BaseModel):
    project_id: str
    feature_id: str | None = None
    status: str = "pass"  # pass | warn | fail
    checks: list[CheckResult] = Field(default_factory=list)
    total_findings: int = 0
    waived_findings: int = 0
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())

    @classmethod
    def from_checks(
        cls, project_id: str, checks: list[CheckResult], feature_id: str | None = None
    ) -> GuardrailReport:
        has_fail = any(c.has_fails for c in checks)
        has_warn = any(c.has_warns for c in checks)
        status = "fail" if has_fail else ("warn" if has_warn else "pass")
        all_findings = [f for c in checks for f in c.findings]
        waived = sum(1 for f in all_findings if f.waived)
        return cls(
            project_id=project_id,
            feature_id=feature_id,
            status=status,
            checks=checks,
            total_findings=len(all_findings),
            waived_findings=waived,
        )

    def to_markdown(self) -> str:
        icon = "✓" if self.status == "pass" else ("⚠" if self.status == "warn" else "✗")
        lines = [
            f"# Guardrail Report: {self.status.upper()} {icon}",
            "",
            f"**Project:** {self.project_id} | **Feature:** {self.feature_id or 'none'}",
            f"**Findings:** {self.total_findings} total ({self.waived_findings} waived)",
            "",
            "| Check | Status | Summary | Findings |",
            "|---|---|---|---|",
        ]
        for c in self.checks:
            c_icon = "✓" if c.status == "pass" else ("⚠" if c.status == "warn" else "✗")
            n = len(c.findings)
            waived_n = sum(1 for f in c.findings if f.waived)
            count_str = f"{n} ({waived_n} waived)" if waived_n else str(n)
            lines.append(f"| {c.check} | {c_icon} {c.status} | {c.summary} | {count_str} |")

        lines.append("")
        actionable = [f for c in self.checks for f in c.findings if not f.waived]
        if actionable:
            lines.extend(["## Actionable Findings", ""])
            for f in actionable:
                loc = f" (`{f.location}`)" if f.location else ""
                lines.append(f"- **[{f.severity.upper()}]** {f.check}: {f.message}{loc}")
                if f.fix_hint:
                    lines.append(f"  *Fix:* {f.fix_hint}")
            lines.append("")

        waived = [f for c in self.checks for f in c.findings if f.waived]
        if waived:
            lines.extend(["## Waived Findings", ""])
            for f in waived:
                lines.append(f"- `{f.id}` ({f.check}): {f.message} — *Reason:* {f.waiver_reason}")
            lines.append("")

        return "\n".join(lines)
