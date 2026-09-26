"""`agent-factory.yaml` schema.

Secrets never live here: connection settings name the *environment variables*
that hold them (`password_env: NEO4J_PASSWORD`), and a validator rejects
literal secret values so a pasted password fails loudly instead of being committed.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CONFIG_FILENAME = "agent-factory.yaml"
SUPPORTED_AGENTS = ("claude-code", "cursor", "vscode", "codex")
SUPPORTED_LANGUAGES = ("typescript", "javascript", "python")

_SECRET_KEY = re.compile(r"(?:^|_)(?:password|passwd|secret|api_?key|token|credentials?)$", re.IGNORECASE)
_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")
_SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")


class ConfigError(ValueError):
    """The configuration is missing or invalid (CLI exit code 2)."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class ProjectConfig(_Strict):
    id: str
    name: str
    languages: list[str] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not _SLUG.match(v):
            raise ValueError("project.id must be a lowercase slug (a-z, 0-9, '-', '_')")
        return v

    @field_validator("languages")
    @classmethod
    def _langs(cls, v: list[str]) -> list[str]:
        unknown = sorted(set(v) - set(SUPPORTED_LANGUAGES))
        if unknown:
            raise ValueError(f"unsupported languages {unknown}; supported: {list(SUPPORTED_LANGUAGES)}")
        return v


class IndexConfig(_Strict):
    include: list[str] = Field(default_factory=lambda: ["**"])
    exclude: list[str] = Field(
        default_factory=lambda: [
            "**/node_modules/**",
            "**/dist/**",
            "**/build/**",
            "**/.next/**",
            "**/coverage/**",
            "**/.venv/**",
            "**/__pycache__/**",
            "**/vendor/**",
        ]
    )
    max_file_kb: int = Field(default=256, ge=1, le=10_240)
    git_history_commits: int = Field(default=500, ge=0, le=100_000)


class Neo4jTarget(_Strict):
    uri_env: str
    user_env: str
    password_env: str
    database_env: str | None = None

    @field_validator("uri_env", "user_env", "password_env", "database_env")
    @classmethod
    def _env_name(cls, v: str | None) -> str | None:
        if v is not None and not _ENV_NAME.match(v):
            raise ValueError(f"{v!r} is not an environment variable name (expected e.g. NEO4J_PASSWORD)")
        return v


class Neo4jConfig(_Strict):
    domain: Neo4jTarget = Field(
        default_factory=lambda: Neo4jTarget(
            uri_env="NEO4J_URI", user_env="NEO4J_USERNAME", password_env="NEO4J_PASSWORD", database_env="NEO4J_DATABASE"
        )
    )
    memory: Neo4jTarget = Field(
        default_factory=lambda: Neo4jTarget(
            uri_env="MVP_NEO4J_URI", user_env="MVP_NEO4J_USERNAME", password_env="MVP_NEO4J_PASSWORD"
        )
    )


class EmbeddingsConfig(_Strict):
    provider: str = "openai"
    model: str = "text-embedding-3-small"
    dimensions: int = Field(default=1536, ge=8, le=4096)


class LlmConfig(_Strict):
    model: str = "gpt-5.2"
    enabled: bool = True


class ChecksConfig(_Strict):
    test: str | None = None
    build: str | None = None
    lint: str | None = None


class ValidationConfig(_Strict):
    auto_validate_min_confidence: float = Field(default=0.75, ge=0.0, le=1.0)
    auto_validate_min_support: int = Field(default=3, ge=1)


class AgentFactoryConfig(_Strict):
    version: int = 1
    project: ProjectConfig
    index: IndexConfig = Field(default_factory=IndexConfig)
    neo4j: Neo4jConfig = Field(default_factory=Neo4jConfig)
    embeddings: EmbeddingsConfig = Field(default_factory=EmbeddingsConfig)
    llm: LlmConfig = Field(default_factory=LlmConfig)
    checks: ChecksConfig = Field(default_factory=ChecksConfig)
    validation: ValidationConfig = Field(default_factory=ValidationConfig)
    agents: list[str] = Field(default_factory=lambda: ["claude-code", "cursor", "vscode"])

    @model_validator(mode="before")
    @classmethod
    def _no_secret_literals(cls, data: Any) -> Any:
        offenders = list(_secret_literal_paths(data))
        if offenders:
            raise ValueError(
                f"secret value at {', '.join(offenders)}: store secrets in .env and reference them "
                "by env var name (e.g. password_env: NEO4J_PASSWORD)"
            )
        return data

    @field_validator("version")
    @classmethod
    def _version(cls, v: int) -> int:
        if v != 1:
            raise ValueError(f"unsupported config version {v}; this Agent Factory reads version 1")
        return v

    @field_validator("agents")
    @classmethod
    def _agents(cls, v: list[str]) -> list[str]:
        unknown = sorted(set(v) - set(SUPPORTED_AGENTS))
        if unknown:
            raise ValueError(f"unknown agents {unknown}; supported: {list(SUPPORTED_AGENTS)}")
        return v


def _secret_literal_paths(data: Any, prefix: str = "") -> list[str]:
    """Paths of keys that look like secrets and carry a literal value."""
    found: list[str] = []
    if isinstance(data, dict):
        for key, value in data.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(value, (dict, list)):
                found.extend(_secret_literal_paths(value, path))
            elif isinstance(key, str) and _SECRET_KEY.search(key) and value not in (None, ""):
                found.append(path)
    elif isinstance(data, list):
        for i, item in enumerate(data):
            found.extend(_secret_literal_paths(item, f"{prefix}[{i}]"))
    return found
