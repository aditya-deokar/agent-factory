"""Secret detection and redaction.

Every piece of text Agent Factory persists (docstrings, doc chunks, commit
messages, knowledge claims, captured stdout) passes through `redact()` first.
Files that are secret by nature (`.env`, keys, state files) are never read at
all: see `is_secret_file()`.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import PurePosixPath

__all__ = ["Finding", "RedactionResult", "contains_secret", "is_secret_file", "redact"]


@dataclass(frozen=True)
class Finding:
    kind: str
    start: int
    end: int


@dataclass
class RedactionResult:
    text: str
    findings: list[Finding] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.findings


# (kind, pattern). A named group `secret` limits the replacement to that group,
# so `password = "hunter22"` keeps its key and loses only the value.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "private_key",
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)"),
    ),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("openai_key", re.compile(r"\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}")),
    ("graphacademy_key", re.compile(r"\bga-[A-Za-z0-9_-]{16,}")),
    ("github_token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})")),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("url_credentials", re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s:/@]+:(?P<secret>[^\s@/]+)@")),
    (
        "env_assignment",
        re.compile(
            r"\b[A-Z0-9_]*(?:PASSWORD|PASSWD|SECRET|API_KEY|APIKEY|ACCESS_KEY|TOKEN)\b\s*[=:]\s*"
            r"[\"']?(?P<secret>[^\s\"',;]{4,})"
        ),
    ),
    (
        "quoted_assignment",
        re.compile(
            r"(?i)\b(?:password|passwd|pwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret)"
            r"\b[\"']?\s*[:=]\s*[\"'](?P<secret>[^\"'\s]{6,})[\"']"
        ),
    ),
]

_CANDIDATE = re.compile(r"[A-Za-z0-9+/=_-]{32,}")
_PLACEHOLDER = re.compile(
    r"^(?:\.{3,}|x{3,}|\*{3,}|<.*|\[REDACTED:.*|\$\{[^}]*\}|\$[A-Z_]+|your[-_].*|changeme|example.*|placeholder|null|none|"
    r"true|false|string|process\.env.*|os\.environ.*|env\(.*)$",
    re.IGNORECASE,
)

_SECRET_FILE_NAMES = {".npmrc", ".pypirc", ".netrc", ".git-credentials", ".htpasswd"}
_SECRET_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".jks", ".keystore", ".tfstate", ".tfstate.backup", ".asc")
_SECRET_NAME_RE = re.compile(
    r"^(?:\.env(?:\..*)?|id_(?:rsa|dsa|ecdsa|ed25519)(?:\.pub)?|credentials.*\.json|service[-_]account.*\.json|"
    r"secrets?\.(?:ya?ml|json|toml))$",
    re.IGNORECASE,
)


def shannon_entropy(s: str) -> float:
    if not s:
        return 0.0
    counts = Counter(s)
    n = len(s)
    return -sum(c / n * math.log2(c / n) for c in counts.values())


def _looks_random(token: str) -> bool:
    # Hex digests top out at 4.0 bits/char and are not secrets; identifiers rarely
    # mix digits with both cases. Require all three signals.
    has_digit = any(ch.isdigit() for ch in token)
    has_upper = any(ch.isupper() for ch in token)
    has_lower = any(ch.islower() for ch in token)
    return has_digit and has_upper and has_lower and shannon_entropy(token) > 4.2


def _spans(text: str) -> list[Finding]:
    found: list[Finding] = []
    for kind, pattern in _PATTERNS:
        for m in pattern.finditer(text):
            if "secret" in pattern.groupindex and m.group("secret") is not None:
                value = m.group("secret")
                if _PLACEHOLDER.match(value.strip("\"'")):
                    continue
                found.append(Finding(kind, m.start("secret"), m.end("secret")))
            else:
                found.append(Finding(kind, m.start(), m.end()))
    for m in _CANDIDATE.finditer(text):
        if _looks_random(m.group()):
            found.append(Finding("high_entropy", m.start(), m.end()))
    # Merge overlaps, keeping the first (most specific) kind.
    found.sort(key=lambda f: (f.start, -(f.end - f.start)))
    merged: list[Finding] = []
    for f in found:
        if merged and f.start < merged[-1].end:
            last = merged[-1]
            merged[-1] = Finding(last.kind, last.start, max(last.end, f.end))
        else:
            merged.append(f)
    return merged


def redact(text: str) -> RedactionResult:
    """Replace every detected secret with `[REDACTED:<kind>]`."""
    if not text:
        return RedactionResult(text or "")
    findings = _spans(text)
    if not findings:
        return RedactionResult(text)
    out: list[str] = []
    pos = 0
    for f in findings:
        out.append(text[pos : f.start])
        out.append(f"[REDACTED:{f.kind}]")
        pos = f.end
    out.append(text[pos:])
    return RedactionResult("".join(out), findings)


def contains_secret(text: str) -> bool:
    return bool(text) and bool(_spans(text))


def is_secret_file(path: str) -> bool:
    """True for files that must never be read or indexed (§30)."""
    name = PurePosixPath(path.replace("\\", "/")).name
    lower = name.lower()
    if lower in _SECRET_FILE_NAMES or lower.endswith(_SECRET_SUFFIXES):
        return True
    return bool(_SECRET_NAME_RE.match(name))
