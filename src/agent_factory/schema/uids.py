"""Deterministic node ids: `<project_id>:<kind>:<key>`.

Deterministic ids make every write an idempotent MERGE, so re-running an audit
never duplicates nodes. Paths inside ids are repo-relative POSIX.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import UTC, datetime

from ..common.paths import to_posix
from .model import KnowledgeKind

_SLUG = re.compile(r"[^a-z0-9]+")


def slug(text: str, max_len: int = 60) -> str:
    s = _SLUG.sub("-", text.lower()).strip("-")
    return (s or "item")[:max_len].rstrip("-")


def module_uid(project: str, path: str) -> str:
    return f"{project}:mod:{to_posix(path) or '.'}"


def file_uid(project: str, path: str) -> str:
    return f"{project}:file:{to_posix(path)}"


def symbol_uid(project: str, path: str, qualname: str) -> str:
    return f"{project}:sym:{to_posix(path)}#{qualname}"


def knowledge_uid(project: str, kind: KnowledgeKind | str, key: str) -> str:
    return f"{project}:k:{KnowledgeKind(kind).value}:{slug(key)}"


def feature_id(name: str, when: datetime | None = None) -> str:
    when = when or datetime.now(UTC)
    return f"feat_{when:%Y%m%d}_{slug(name, 40)}"


def feature_uid(project: str, fid: str) -> str:
    return f"{project}:feat:{fid}"


def evidence_uid(project: str, *parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:16]
    return f"{project}:ev:{digest}"


def commit_uid(project: str, sha: str) -> str:
    return f"{project}:commit:{sha}"


def doc_uid(project: str, path: str) -> str:
    return f"{project}:doc:{to_posix(path)}"


def chunk_uid(project: str, path: str, index: int) -> str:
    return f"{project}:chunk:{to_posix(path)}#{index}"


def audit_run_uid(project: str, when: datetime | None = None) -> str:
    when = when or datetime.now(UTC)
    return f"{project}:run:{when:%Y%m%dT%H%M%S%f}"


def event_uid(project: str) -> str:
    return f"{project}:event:{uuid.uuid4().hex}"


def project_of(uid: str) -> str:
    return uid.split(":", 1)[0]
