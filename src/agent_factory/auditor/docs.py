"""Markdown docs: chunks for GraphRAG, ADRs as decisions, imperative rules as constraint candidates."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from ..common.redact import redact
from ..schema.model import KnowledgeStatus

_ADR_DIR = re.compile(r"(^|/)(adr|adrs|decisions|architecture/decisions)/", re.IGNORECASE)
_ADR_ID = re.compile(r"(?:^|[^a-z])adr[-_ ]?(\d{1,4})|^(\d{3,4})[-_]", re.IGNORECASE)
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_RULE = re.compile(r"\b(must|never|always|do not|don't|should not|shouldn't|only)\b", re.IGNORECASE)
_SUPERSEDED_BY = re.compile(r"superseded\s+by\s+\[?adr[-_ ]?(\d{1,4})", re.IGNORECASE)
_SUPERSEDES = re.compile(r"\bsupersedes\s+\[?adr[-_ ]?(\d{1,4})", re.IGNORECASE)
_STATUS_MAP = {
    "accepted": KnowledgeStatus.VALIDATED,
    "approved": KnowledgeStatus.VALIDATED,
    "adopted": KnowledgeStatus.VALIDATED,
    "proposed": KnowledgeStatus.CANDIDATE,
    "draft": KnowledgeStatus.CANDIDATE,
    "deprecated": KnowledgeStatus.DEPRECATED,
    "superseded": KnowledgeStatus.SUPERSEDED,
    "rejected": KnowledgeStatus.REJECTED,
}
MAX_CHUNK_CHARS = 3200  # ~800 tokens
OVERLAP_CHARS = 400


@dataclass
class Chunk:
    index: int
    text: str
    heading: str
    line_start: int


@dataclass
class Adr:
    adr_id: str
    title: str
    status_text: str
    status: KnowledgeStatus
    path: str
    context: str = ""
    decision: str = ""
    consequences: str = ""
    supersedes: list[str] = field(default_factory=list)
    superseded_by: list[str] = field(default_factory=list)
    line_end: int = 1


@dataclass
class ProseRule:
    text: str
    path: str
    line: int


@dataclass
class DocResult:
    path: str
    kind: str
    title: str
    chunks: list[Chunk]
    adr: Adr | None = None
    rules: list[ProseRule] = field(default_factory=list)


def doc_kind(path: str) -> str:
    name = PurePosixPath(path).name.lower()
    if _ADR_DIR.search(path) or name.startswith("adr"):
        return "adr"
    if name.startswith("readme"):
        return "readme"
    if name.startswith(("contributing", "architecture", "conventions", "style")) or "/architecture" in path.lower():
        return "guide"
    return "spec"


def adr_number(text: str) -> str | None:
    m = _ADR_ID.search(text)
    if not m:
        return None
    return f"ADR-{int(m.group(1) or m.group(2)):03d}"


def _sections(lines: list[str]) -> list[tuple[str, int, list[str]]]:
    """-> [(heading, start_line, body_lines)]"""
    out: list[tuple[str, int, list[str]]] = []
    heading, start = "", 1
    body: list[str] = []
    in_code = False
    for i, line in enumerate(lines, start=1):
        if line.strip().startswith("```"):
            in_code = not in_code
        m = _HEADING.match(line) if not in_code else None
        if m:
            if body or heading:
                out.append((heading, start, body))
            heading, start, body = m.group(2).strip(), i, [line]
        else:
            body.append(line)
    if body or heading:
        out.append((heading, start, body))
    return out


def chunk_markdown(text: str) -> list[Chunk]:
    chunks: list[Chunk] = []
    buf, buf_heading, buf_start = "", "", 1
    for heading, start, body in _sections(text.splitlines()):
        section = "\n".join(body).strip()
        if not section:
            continue
        if buf and len(buf) + len(section) + 2 > MAX_CHUNK_CHARS:
            chunks.append(Chunk(len(chunks), buf, buf_heading, buf_start))
            buf = ""
        if not buf:
            buf_heading, buf_start = heading, start
        buf = f"{buf}\n\n{section}" if buf else section
        while len(buf) > MAX_CHUNK_CHARS:
            cut = buf.rfind("\n", 0, MAX_CHUNK_CHARS)
            cut = cut if cut > MAX_CHUNK_CHARS // 2 else MAX_CHUNK_CHARS
            chunks.append(Chunk(len(chunks), buf[:cut].strip(), buf_heading, buf_start))
            buf = buf[max(0, cut - OVERLAP_CHARS) :]
    if buf.strip():
        chunks.append(Chunk(len(chunks), buf.strip(), buf_heading, buf_start))
    for c in chunks:
        c.text = redact(c.text).text
    return chunks


def parse_adr(path: str, text: str) -> Adr | None:
    lines = text.splitlines()
    title_line = next((ln for ln in lines if ln.startswith("# ")), "")
    adr_id = adr_number(PurePosixPath(path).name) or adr_number(title_line)
    if adr_id is None:
        return None
    title = re.sub(r"^#\s*(adr[-_ ]?\d+\s*[:.-]\s*)?", "", title_line, flags=re.IGNORECASE).strip() or adr_id
    sections = {
        h.lower(): "\n".join(b[1:] if b and _HEADING.match(b[0]) else b).strip() for h, _, b in _sections(lines)
    }
    status_text = sections.get("status", "")
    if not status_text:
        m = re.search(r"(?im)^\s*\**status\**\s*[:=]\s*(.+)$", text)
        status_text = m.group(1) if m else "accepted"
    first_word = re.sub(r"[^a-z ]", " ", status_text.lower()).split()
    status = next((_STATUS_MAP[w] for w in first_word if w in _STATUS_MAP), KnowledgeStatus.CANDIDATE)
    superseded_by = [f"ADR-{int(n):03d}" for n in _SUPERSEDED_BY.findall(text)]
    if superseded_by:
        status = KnowledgeStatus.SUPERSEDED
    return Adr(
        adr_id=adr_id,
        title=redact(title).text,
        status_text=status_text.splitlines()[0].strip() if status_text else "",
        status=status,
        path=path,
        context=redact(sections.get("context", "")).text[:1000],
        decision=redact(sections.get("decision", "")).text[:1000],
        consequences=redact(sections.get("consequences", "")).text[:1000],
        supersedes=[f"ADR-{int(n):03d}" for n in _SUPERSEDES.findall(text)],
        superseded_by=superseded_by,
        line_end=len(lines),
    )


def prose_rules(path: str, text: str, limit: int = 20) -> list[ProseRule]:
    rules: list[ProseRule] = []
    in_code = False
    for i, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code = not in_code
            continue
        if in_code or not stripped or stripped.startswith(("#", "|", ">")):
            continue
        sentence = re.sub(r"^([-*+]|\d+\.)\s+", "", stripped)
        if _RULE.search(sentence) and 12 <= len(sentence) <= 240:
            rules.append(ProseRule(redact(sentence).text, path, i))
        if len(rules) >= limit:
            break
    return rules


def parse_doc(root: Path, path: str) -> DocResult:
    text = (root / path).read_text(encoding="utf-8", errors="replace")
    kind = doc_kind(path)
    title = next((ln[2:].strip() for ln in text.splitlines() if ln.startswith("# ")), PurePosixPath(path).stem)
    result = DocResult(path, kind, redact(title).text, chunk_markdown(text))
    if kind == "adr":
        result.adr = parse_adr(path, text)
    elif kind in ("guide", "readme"):
        result.rules = prose_rules(path, text)
    return result
