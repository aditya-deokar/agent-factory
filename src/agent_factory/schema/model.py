"""The domain graph vocabulary (plan: phase-02-graph-schema.md).

Cypher cannot parameterise labels or relationship types, so every label or
type that reaches a query string comes from these enums, never from input.
"""

from __future__ import annotations

from enum import StrEnum


class Label(StrEnum):
    PROJECT = "Project"
    MODULE = "Module"
    FILE = "File"
    TEST_FILE = "TestFile"
    SYMBOL = "Symbol"
    KNOWLEDGE = "Knowledge"
    PATTERN = "Pattern"
    DECISION = "Decision"
    CONSTRAINT = "Constraint"
    FEATURE = "Feature"
    EVIDENCE = "Evidence"
    COMMIT = "Commit"
    DOC = "Doc"
    DOC_CHUNK = "DocChunk"
    AUDIT_RUN = "AuditRun"
    MEMORY_EVENT = "MemoryEvent"
    SCHEMA_VERSION = "SchemaVersion"


class Role(StrEnum):
    """Architectural role labels added to :Symbol nodes."""

    SERVICE = "Service"
    REPOSITORY = "Repository"
    CONTROLLER = "Controller"
    ROUTE = "Route"
    COMPONENT = "Component"
    HOOK = "Hook"
    WORKER = "Worker"
    QUEUE = "Queue"
    MIDDLEWARE = "Middleware"
    MODEL = "Model"
    VALIDATOR = "Validator"
    INTEGRATION = "Integration"


class Rel(StrEnum):
    CONTAINS = "CONTAINS"
    DEFINES = "DEFINES"
    HAS_MEMBER = "HAS_MEMBER"
    IMPORTS = "IMPORTS"
    DEPENDS_ON = "DEPENDS_ON"
    USES = "USES"
    CALLS = "CALLS"
    EXTENDS = "EXTENDS"
    IMPLEMENTS = "IMPLEMENTS"
    ACCESSES = "ACCESSES"
    HANDLES = "HANDLES"
    COVERS = "COVERS"
    FOLLOWS = "FOLLOWS"
    ESTABLISHES = "ESTABLISHES"
    CONSTRAINED_BY = "CONSTRAINED_BY"
    SUPERSEDES = "SUPERSEDES"
    SUPPORTED_BY = "SUPPORTED_BY"
    CONTRADICTED_BY = "CONTRADICTED_BY"
    REFERENCES = "REFERENCES"
    TOUCHES = "TOUCHES"
    CO_CHANGES = "CO_CHANGES"
    MODIFIES = "MODIFIES"
    INTRODUCES = "INTRODUCES"
    REUSES = "REUSES"
    TESTED_BY = "TESTED_BY"
    IMPLEMENTED_IN = "IMPLEMENTED_IN"
    HAS_EVIDENCE = "HAS_EVIDENCE"
    FOLLOWED = "FOLLOWED"
    CREATED = "CREATED"
    FROM_DOC = "FROM_DOC"
    NEXT_CHUNK = "NEXT_CHUNK"
    DESCRIBES = "DESCRIBES"


class KnowledgeKind(StrEnum):
    PATTERN = "pattern"
    DECISION = "decision"
    CONSTRAINT = "constraint"

    @property
    def label(self) -> Label:
        return {"pattern": Label.PATTERN, "decision": Label.DECISION, "constraint": Label.CONSTRAINT}[self.value]


class KnowledgeStatus(StrEnum):
    CANDIDATE = "candidate"
    VALIDATED = "validated"
    DEPRECATED = "deprecated"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"


class KnowledgeSource(StrEnum):
    AUDITOR = "auditor"
    ADR = "adr"
    AGENT = "agent"
    HUMAN = "human"


class EvidenceKind(StrEnum):
    CODE_REF = "code_ref"
    TEST_RUN = "test_run"
    BUILD = "build"
    LINT = "lint"
    SCREENSHOT = "screenshot"
    RECORDING = "recording"
    VISUAL_DIFF = "visual_diff"
    API_CHECK = "api_check"
    GUARDRAIL_REPORT = "guardrail_report"
    DIFF = "diff"
    REVIEW = "review"
    ADR = "adr"


class FeatureStatus(StrEnum):
    PLANNING = "planning"
    IN_PROGRESS = "in_progress"
    VERIFYING = "verifying"
    DONE = "done"
    ABANDONED = "abandoned"


# Relationship types a Feature may point at symbols/knowledge with.
FEATURE_RELS = frozenset(
    {
        Rel.MODIFIES,
        Rel.INTRODUCES,
        Rel.REUSES,
        Rel.TESTED_BY,
        Rel.IMPLEMENTED_IN,
        Rel.HAS_EVIDENCE,
        Rel.FOLLOWED,
        Rel.CREATED,
    }
)


def rel(value: str | Rel) -> Rel:
    """Validate a relationship type. Raises ValueError for anything not in the whitelist."""
    try:
        return Rel(value)
    except ValueError as error:
        raise ValueError(f"unknown relationship type {value!r}") from error


def role(value: str | Role) -> Role:
    try:
        return Role(value)
    except ValueError as error:
        raise ValueError(f"unknown role label {value!r}") from error
