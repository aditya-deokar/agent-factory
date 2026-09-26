"""Spike S2: neo4j-agent-memory 0.6.0 round trip against LOCAL Neo4j.

Never point this at the shared workshop memory instance: it writes test data.
Uses the OpenAI-compatible proxy for embeddings (or --offline: a deterministic
hashed bag-of-words embedder); extraction is disabled to keep the spike fast.

    uv run python docs/spikes/code/s2_agent_memory.py [--offline]
"""

import asyncio
import hashlib
import math
import os
import re
import sys
import time

from dotenv import load_dotenv
from neo4j import GraphDatabase
from neo4j_agent_memory import MemoryClient, MemorySettings
from neo4j_agent_memory.embeddings.base import BaseEmbedder
from neo4j_agent_memory.config import EmbeddingConfig, ExtractionConfig, ExtractorType, Neo4jConfig

load_dotenv()
URI = os.environ["NEO4J_URI"]
assert "localhost" in URI or "127.0.0.1" in URI, "S2 writes data: run it against local Neo4j only"
AUTH = (os.environ["NEO4J_USERNAME"], os.environ["NEO4J_PASSWORD"])
SESSION = "spike:s2"
OFFLINE = "--offline" in sys.argv


class HashEmbedder(BaseEmbedder):
    """Hashed bag-of-words: lexical overlap gives similarity, no network."""

    @property
    def dimensions(self) -> int:
        return 1536

    async def embed(self, text: str) -> list[float]:
        v = [0.0] * 1536
        for tok in re.findall(r"[a-z0-9]+", text.lower()):
            h = int.from_bytes(hashlib.sha256(tok.encode()).digest()[:4], "big")
            v[h % 1536] += 1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]


def labels() -> set[str]:
    with GraphDatabase.driver(URI, auth=AUTH) as d:
        recs, _, _ = d.execute_query("CALL db.labels() YIELD label RETURN label")
        return {r["label"] for r in recs}


async def main() -> None:
    before = labels()
    print("EmbeddingConfig fields:", list(EmbeddingConfig.model_fields))
    settings = MemorySettings(
        neo4j=Neo4jConfig(uri=URI, username=AUTH[0], password=AUTH[1]),
        embedding=EmbeddingConfig(api_key=os.environ["OPENAI_API_KEY"]),
        extraction=ExtractionConfig(extractor_type=ExtractorType.NONE),
    )
    t0 = time.perf_counter()
    print("mode:", "offline (HashEmbedder)" if OFFLINE else "online (OpenAI proxy)")
    async with MemoryClient(settings, embedder=HashEmbedder() if OFFLINE else None) as memory:
        await memory.short_term.add_message(
            SESSION, "user", "Add team invitations that reuse the token service.",
            extract_entities=False, extract_relations=False,
        )
        trace = await memory.reasoning.start_trace(SESSION, "Add team invitations")
        step = await memory.reasoning.add_step(trace.id, thought="Check for reusable token code", action="find_reusable")
        await memory.reasoning.record_tool_call(
            step.id, "find_reusable", {"name": "InvitationTokenService"}, result={"top": "TokenService"}
        )
        await memory.reasoning.complete_trace(trace.id, outcome="reused TokenService", success=True)
        await memory.long_term.add_fact("TeamInvitation", "uses", "TokenService")
        await memory.long_term.add_preference("architecture", "Validate route input with Zod schemas")

        hits = await memory.short_term.search_messages("invite teammates", session_id=SESSION, limit=3, threshold=0.3)
        print("search_messages:", [m.content for m in hits])
        similar = await memory.reasoning.get_similar_traces("team invitation feature", limit=3, threshold=0.3)
        print("similar traces:", [(t.task, t.outcome) for t in similar])
        prefs = await memory.long_term.search_preferences("route validation", limit=3, threshold=0.3)
        print("preferences:", [p.preference for p in prefs])
        ctx = await memory.get_context("team invitations", session_id=SESSION)
        print("get_context chars:", len(ctx))
        print(ctx[:600])
    print(f"elapsed: {time.perf_counter() - t0:.1f}s")
    print("labels created by neo4j-agent-memory:", sorted(labels() - before))


asyncio.run(main())
