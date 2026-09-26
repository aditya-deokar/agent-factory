"""Runtime: everything a command or MCP tool needs, created lazily and shared.

The CLI and the MCP server both go through this, so a tool and its CLI twin
always behave the same way.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from functools import cached_property
from pathlib import Path
from typing import Any

from .common.embed import Embedder, make_embedder
from .config import AgentFactoryConfig, load_config
from .db.repos import AuditRunRepo
from .db.stores import GraphStores


class NotAudited(RuntimeError):
    pass


class Runtime:
    def __init__(
        self,
        root: Path,
        config: AgentFactoryConfig,
        stores: GraphStores,
        env: Mapping[str, str] | None = None,
        embedder: Embedder | bool | None = True,
        memory: Any = None,
    ):
        self.root = root
        self.config = config
        self.stores = stores
        self.env = os.environ if env is None else env
        self._embedder_arg = embedder
        self._memory = memory
        self.warnings: list[str] = []

    @classmethod
    def open(cls, root: Path, env: Mapping[str, str] | None = None, **kwargs: Any) -> Runtime:
        config = load_config(root, env=env)
        return cls(root, config, GraphStores.from_config(config, env), env, **kwargs)

    @property
    def project_id(self) -> str:
        return self.config.project.id

    @property
    def store(self) -> Any:
        return self.stores.domain

    def close(self) -> None:
        self.stores.close()

    # -- lazily built parts -------------------------------------------------------------------------------------------

    @cached_property
    def embedder(self) -> Embedder | None:
        if self._embedder_arg is False or self._embedder_arg is None:
            return None
        if self._embedder_arg is not True:
            embedder: Embedder | None = self._embedder_arg
        else:
            cfg = self.config.embeddings
            embedder = make_embedder(
                cfg.provider, cfg.model, cfg.dimensions, self.env, self.root / ".agent-factory" / "cache"
            )
        if embedder is None:
            return None
        rows = self.store.read(
            "MATCH (s:Symbol {project_id: $p}) WHERE s.embedding_model IS NOT NULL "
            "RETURN s.embedding_model AS m LIMIT 1",
            p=self.project_id,
        )
        if rows and rows[0]["m"] != embedder.model:
            self.warnings.append(
                f"the graph was embedded with {rows[0]['m']} but queries would use {embedder.model}; "
                "semantic search is off until you re-run agent-factory audit"
            )
            return None
        return embedder

    @cached_property
    def memory(self) -> Any:
        if self._memory is not None:
            return self._memory
        from .memory.agent_memory import make_agent_memory

        return make_agent_memory(self.config, self.env)

    def engine(self) -> Any:
        from .context.engine import ContextEngine

        return ContextEngine(self.store, self.project_id, self.root, self.embedder, self.memory)

    def reuse(self) -> Any:
        from .context.reuse import ReuseDetector

        return ReuseDetector(self.store, self.project_id, self.embedder)

    def knowledge(self) -> Any:
        from .memory.knowledge import KnowledgeService

        return KnowledgeService(self.store, self.project_id, self.root, self.config.validation)

    def features(self) -> Any:
        from .workflow.feature import FeatureService

        return FeatureService(self)

    def evidence(self, feature_id: str) -> Any:
        from .evidence.store import EvidenceStore

        return EvidenceStore(self.root, feature_id)

    def require_audit(self) -> dict[str, Any]:
        last = AuditRunRepo(self.store, self.project_id).last()
        if last is None:
            raise NotAudited(f"No audit found for project {self.project_id}. Run: agent-factory audit")
        return last
