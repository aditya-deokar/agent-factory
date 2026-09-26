// ==============================================================================
// Agent Factory: Curated Neo4j Browser Queries for Demo & Judging
// ==============================================================================

// 1. Architecture Flow: Clean Layering from Route to Controller to Service to Repository
// Demonstrates that code structure is modeled as a connected Property Graph.
MATCH path = (r:Symbol {kind: 'route'})-[:HANDLES]->(c:Symbol)-[:CALLS|USES*1..2]->(s:Symbol {kind: 'class'})-[:CALLS|USES]->(repo:Symbol)-[:ACCESSES]->(m:Symbol {kind: 'model'})
RETURN path
LIMIT 25;

// 2. Multi-Hop Blast Radius & Impact Analysis (TokenService)
// Shows how sub-millisecond graph traversals reveal all upstream consumers before editing code.
MATCH (target:Symbol {name: 'TokenService', project_id: 'teamapp'})
OPTIONAL MATCH (caller:Symbol)-[r:CALLS|USES]->(target)
OPTIONAL MATCH (owner:Symbol)-[:HAS_MEMBER]->(target)
RETURN target.name AS Symbol, target.path AS Path, type(r) AS Relationship, caller.name AS Dependent, caller.path AS DependentPath;

// 3. Architectural Constraints & Active Violations (ADR-002)
// Visualizes validated constraints enforced on the codebase.
MATCH (c:Constraint {project_id: 'teamapp', status: 'validated'})
OPTIONAL MATCH (violator:Symbol)-[:CONSTRAINED_BY]->(c)
RETURN c.title AS Constraint, c.rule_type AS RuleType, c.claim AS Claim, collect(violator.name) AS ActiveViolators;

// 4. Feature Memory Subgraph: The "Closed Loop"
// Proves that a completed feature records introduces, modifies, reuses, tests, and evidence in Neo4j.
MATCH (f:Feature {project_id: 'teamapp'})
OPTIONAL MATCH (f)-[r1:INTRODUCES]->(intro:Symbol)
OPTIONAL MATCH (f)-[r2:REUSES]->(reuse:Symbol)
OPTIONAL MATCH (f)-[r3:TESTED_BY]->(test:File)
OPTIONAL MATCH (f)-[r4:HAS_EVIDENCE]->(ev:Evidence)
RETURN f.name AS Feature, f.status AS Status,
       collect(DISTINCT intro.name) AS IntroducedSymbols,
       collect(DISTINCT reuse.name) AS ReusedSymbols,
       collect(DISTINCT test.path) AS Tests,
       collect(DISTINCT ev.summary) AS VerifiedEvidence;

// 5. Institutional Knowledge Graph with Cryptographic Evidence Anchors
// Pinned knowledge items linked to exact source lines and confidence scores.
MATCH (k:Knowledge {project_id: 'teamapp', status: 'validated'})
RETURN k.kind AS Kind, k.title AS Title, k.confidence AS Confidence, k.source AS Source, k.status AS Status
ORDER BY k.confidence DESC;
