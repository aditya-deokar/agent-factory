"""Evidence store (spec §23): collects, hashes, verifies, and stores proof artifacts."""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ..common.redact import redact
from ..schema.uids import feature_uid


class EvidenceItem(BaseModel):
    id: str = Field(description="Unique evidence identifier, e.g. ev_diff_stat")
    kind: str = Field(description="test | build | lint | diff | guardrail | screenshot | recording | other")
    path: str = Field(description="POSIX relative path inside evidence directory")
    sha256: str = Field(description="SHA-256 of file contents (LF normalized)")
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    summary: str = Field(description="Short human summary of the evidence")
    command: str | None = None
    exit_code: int | None = None
    duration: float | None = None


def sha256_of_bytes(data: bytes) -> str:
    """Normalize CRLF to LF for deterministic hashes across Windows and Linux."""
    normalized = data.replace(b"\r\n", b"\n")
    return hashlib.sha256(normalized).hexdigest()


def sha256_of_file(path: Path) -> str:
    return sha256_of_bytes(path.read_bytes())


class EvidenceStore:
    def __init__(self, root: Path, feature_id: str):
        self.root = root
        self.feature_id = feature_id
        self.dir = root / ".agent-factory" / "evidence" / feature_id
        self.manifest_path = self.dir / "manifest.json"

    def _load_manifest(self) -> list[EvidenceItem]:
        if not self.manifest_path.exists():
            return []
        try:
            raw = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            return [EvidenceItem.model_validate(item) for item in raw]
        except (OSError, json.JSONDecodeError):
            return []

    def _save_manifest(self, items: list[EvidenceItem]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.manifest_path.write_text(
            json.dumps([item.model_dump() for item in items], indent=2),
            encoding="utf-8",
        )

    def list_items(self) -> list[EvidenceItem]:
        return self._load_manifest()

    def add_artifact(
        self,
        kind: str,
        source_path: Path,
        summary: str,
        rel_dest: str | None = None,
    ) -> EvidenceItem:
        """Register an existing artifact file into the evidence store."""
        if not source_path.exists():
            raise FileNotFoundError(f"evidence file '{source_path}' does not exist")

        dest_rel = rel_dest or f"media/{source_path.name}"
        dest_abs = self.dir / dest_rel
        dest_abs.parent.mkdir(parents=True, exist_ok=True)

        # Redact text files
        try:
            content_str = source_path.read_text(encoding="utf-8")
            redacted_str = redact(content_str).text
            dest_abs.write_text(redacted_str, encoding="utf-8")
        except UnicodeDecodeError:
            # Binary file (screenshot, video)
            shutil.copy2(source_path, dest_abs)

        file_hash = sha256_of_file(dest_abs)
        item_id = f"ev_{kind}_{hashlib.sha256(dest_rel.encode()).hexdigest()[:8]}"

        item = EvidenceItem(
            id=item_id,
            kind=kind,
            path=dest_rel.replace("\\", "/"),
            sha256=file_hash,
            summary=summary,
        )

        items = [i for i in self._load_manifest() if i.path != item.path]
        items.append(item)
        self._save_manifest(items)
        return item

    def record_text(
        self,
        kind: str,
        rel_dest: str,
        content: str,
        summary: str,
        command: str | None = None,
        exit_code: int | None = None,
        duration: float | None = None,
    ) -> EvidenceItem:
        """Write text content into the evidence store (redacting secrets)."""
        dest_abs = self.dir / rel_dest
        dest_abs.parent.mkdir(parents=True, exist_ok=True)

        clean_content = redact(content).text
        dest_abs.write_text(clean_content, encoding="utf-8")

        file_hash = sha256_of_file(dest_abs)
        item_id = f"ev_{kind}_{hashlib.sha256(rel_dest.encode()).hexdigest()[:8]}"

        item = EvidenceItem(
            id=item_id,
            kind=kind,
            path=rel_dest.replace("\\", "/"),
            sha256=file_hash,
            summary=summary,
            command=command,
            exit_code=exit_code,
            duration=duration,
        )

        items = [i for i in self._load_manifest() if i.path != item.path]
        items.append(item)
        self._save_manifest(items)
        return item

    def verify(self) -> tuple[bool, list[str]]:
        """Verify that all manifest items exist on disk and match their recorded SHA-256."""
        items = self._load_manifest()
        if not items:
            return True, ["No evidence items registered yet."]

        errors: list[str] = []
        for item in items:
            item_file = self.dir / item.path
            if not item_file.exists():
                errors.append(f"Evidence file missing: {item.path} ({item.id})")
                continue
            curr_hash = sha256_of_file(item_file)
            if curr_hash != item.sha256:
                errors.append(
                    f"Evidence hash mismatch (tampered/stale): {item.path} "
                    f"expected {item.sha256[:12]}..., got {curr_hash[:12]}..."
                )

        return len(errors) == 0, errors

    def sync_to_graph(self, store: Any, project_id: str) -> int:
        """Persist evidence items into the Neo4j domain graph linked to the Feature node."""
        items = self._load_manifest()
        if not items or not store:
            return 0

        feat_uid = feature_uid(project_id, self.feature_id)
        count = 0

        for item in items:
            ev_uid = f"{project_id}:ev:{item.id}"
            store.write(
                """
                MERGE (e:Evidence {uid: $uid})
                SET e.project_id = $project_id,
                    e.kind = $kind,
                    e.path = $path,
                    e.sha256 = $sha256,
                    e.summary = $summary,
                    e.command = $command,
                    e.exit_code = $exit_code,
                    e.created_at = $created_at
                WITH e
                MATCH (f:Feature {uid: $feat_uid})
                MERGE (f)-[:HAS_EVIDENCE]->(e)
                """,
                uid=ev_uid,
                project_id=project_id,
                kind=item.kind,
                path=item.path,
                sha256=item.sha256,
                summary=item.summary,
                command=item.command,
                exit_code=item.exit_code,
                created_at=item.created_at,
                feat_uid=feat_uid,
            )
            count += 1
        return count
