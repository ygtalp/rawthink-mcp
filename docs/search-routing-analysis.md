# Search Routing Analysis

## Status

Accepted — hybrid with RRF, as analysed here

## Date

2026-04

## Context


RAWThink has two independent search systems. Should they stay separate, merge into one unified tool, or use intelligent routing?

## Current Architecture

**System 1 — Knowledge Graph (`search_nodes`)**
- Inverted index over JSONL entities
- Token-based matching with kebab-case tokenization
- Scores by token match count * activation decay
- Returns: entities + connected relations
- Best for: "What is X?", "What did I decide about Y?", structured facts

**System 2 — Qdrant Hybrid Search (`search_thoughts`)**
- Dense embeddings (BGE-M3 via Ollama) + BM25 sparse vectors
- RRF fusion of dense and sparse results
- Returns: session/qnote chunks with context
- Best for: "What did I think about X?", "When did I discuss Y?", narrative recall

**Measured overlap (Session 2026-04-12_005):** 8.3% — the two systems are complementary. Graph finds structured knowledge, Qdrant finds narrative context. Different questions, different answers.

---

## Options

### Option A: Keep Separate (Status Quo)

Two tools, user (or Claude) picks which one to call.

**Context cost per search:**
- `search_nodes`: ~500-2000 tokens (10 entities with observations)
- `search_thoughts`: ~1500-5000 tokens (10 chunks of narrative text)
- Combined if both called: ~2000-7000 tokens

**Pros:**
- Explicit — caller knows what they're getting
- No wasted tokens — only call what's needed
- Each tool's response format is clean and typed
- MCP-First Rule in CLAUDE.md already guides when to use which
- Zero implementation work

**Cons:**
- Requires instruction-level routing (Claude must know which to call)
- Two calls when both perspectives are needed
- New users don't know which tool to use for what
- Risk of defaulting to one system and never using the other

**When this breaks:** When a user asks a question that needs both structured facts AND narrative context. Currently requires two separate tool calls.

---

### Option B: Unified `search_all`

Single tool that always queries both systems and returns merged results.

**Context cost per search:**
- Every query: ~2000-7000 tokens (graph results + Qdrant chunks)
- No way to avoid the larger payload
- 10 graph entities + 10 Qdrant chunks = large MCP response every time

**Pros:**
- Simplest user experience — one tool for everything
- Never misses results from either system
- No routing logic needed

**Cons:**
- **Token waste**: most queries only need one system. "What is entropy?" doesn't need 10 narrative chunks. "When did I first discuss this?" doesn't need entity observations.
- **Response bloat**: MCP tool responses are injected into context. 7K tokens per search adds up — 3 searches in a conversation = 21K tokens of search results.
- **Slower**: must wait for both systems (Qdrant embedding is ~100ms, graph is ~50ms at current scale)
- **Harder to read**: mixed result types (entities interleaved with narrative chunks) are confusing
- **Limit competition**: if limit=10, do you return 5+5? 10+10? How do you rank across systems?

**Implementation cost:** Low — wrap both calls, merge results. But the design questions (limit splitting, ranking, response format) are non-trivial.

---

### Option C: Intelligent Routing

Single tool with query analysis that decides which system(s) to call.

**Routing logic options:**

1. **Keyword-based routing:**
   - Query contains entity-like patterns (kebab-case, known entity types) → graph
   - Query is a natural language question → Qdrant
   - Query contains both → both systems

2. **LLM-based routing (expensive):**
   - Small model classifies query intent before routing
   - Adds latency and cost per search

3. **User hint parameter:**
   - `search(query, prefer="graph"|"narrative"|"both")`
   - Default to "both" but allow optimization
   - Caller can reduce context cost when they know what they want

4. **Cascading search:**
   - Try graph first (fast, cheap)
   - If results are insufficient (< 2 relevant), also query Qdrant
   - Reduces average cost while maintaining recall

**Context cost per search:**
- Graph-only queries: ~500-2000 tokens
- Qdrant-only queries: ~1500-5000 tokens
- Both-systems queries: ~2000-7000 tokens
- Average (estimated): ~1500-3000 tokens

**Pros:**
- Best of both worlds — single tool, smart behavior
- Reduces token waste compared to always-both
- Cascading variant is particularly efficient

**Cons:**
- Routing logic is another thing to get wrong
- Keyword-based routing misses nuanced queries
- LLM-based routing adds cost and latency
- More complex to debug ("why didn't it search the graph?")

---

## Response Size Impact

Real measurement from our vault (120 entities, 46 sessions):

| Scenario | Avg tokens | Per conversation (3 searches) |
|----------|-----------|-------------------------------|
| Graph only | ~1,200 | ~3,600 |
| Qdrant only | ~3,000 | ~9,000 |
| Both always | ~4,200 | ~12,600 |
| Routed (estimated) | ~2,000 | ~6,000 |

At 10K entities (benchmark data), graph responses grow because more entities match. Estimated graph response: ~3,000-5,000 tokens for broad queries.

**Context budget consideration:** Claude Code conversations have ~200K context. 12.6K per 3 searches = 6% of context. Not catastrophic, but it compounds with session handoff, MEMORY.md, and conversation history.

---

## Recommendation

**Ship with Option A (separate tools).** Here's why:

1. **8.3% overlap means the systems serve different purposes.** Merging them doesn't add value — it adds confusion.

2. **CLAUDE.md routing works.** The MCP-First Rule + "When a topic comes up" section already tells Claude when to use which tool. This is instruction-level routing and it costs zero tokens.

3. **Token cost matters.** Every unnecessary token in a search response is context budget stolen from the actual conversation. Separate tools = caller pays only for what they need.

4. **Unified search is a premature abstraction.** We'd be building routing logic to solve a problem that CLAUDE.md already solves. When we see real evidence of users consistently calling the wrong tool, then we build routing.

**Post-publish enhancement path:**

If user feedback shows routing confusion:
1. First try: improve CLAUDE.md guidance (zero code change)
2. Then: add `prefer` parameter to search_thoughts (minimal code)
3. Last resort: cascading search tool (Option C, variant 4)

**The principle:** Don't merge things that are different because merging seems cleaner. The systems find different things. Different tools for different purposes is honest architecture.

---

## Open Questions

- Should `search_thoughts` accept a `source_type` filter for graph entities too? (Currently only "session"/"qnote")
- Is there a natural query pattern that always needs both? If so, document it in CLAUDE.md.
- At 10K entities, graph search returns more results — should we add a relevance threshold?
