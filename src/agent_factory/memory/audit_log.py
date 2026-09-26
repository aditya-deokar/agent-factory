"""Audit trail for memory changes (§30): a MemoryEvent node in the graph + a JSONL line on disk."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..common.redact import redact
from ..db.repos import KnowledgeRepo
from ..schema.uids import event_uid

LOG_RELATIVE = Path(".agent-factory") / "audit.log"


class AuditLog:
    def __init__(self, repo: KnowledgeRepo, root: Path | None):
        self.repo = repo
        self.path = (root / LOG_RELATIVE) if root is not None else None

    def record(
        self,
        target_uid: str,
        action: str,
        actor: str,
        *,
        from_status: str | None = None,
        to_status: str | None = None,
        reason: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        event = {
            "uid": event_uid(self.repo.project_id),
            "target_uid": target_uid,
            "action": action,
            "actor": actor,
            "from_status": from_status,
            "to_status": to_status,
            "reason": redact(reason).text if reason else None,
            "details": json.dumps(details, sort_keys=True, default=str) if details else None,
            "at": datetime.now(UTC).isoformat(),
        }
        self.repo.record_event({k: v for k, v in event.items() if v is not None})
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="\n") as fh:
                fh.write(json.dumps(event, ensure_ascii=False) + "\n")
        return event
