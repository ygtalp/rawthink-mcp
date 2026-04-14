# Entity Extraction: Approach Comparison

How should RAWThink extract entities, relations, and observations from session transcripts?

Currently: manual — the LLM reads the session during `/rtclose` and calls graph tools. This works but depends entirely on prompt quality and LLM attention. At scale (long sessions, dense content), extraction quality degrades because the LLM is doing extraction as a side task, not its primary focus.

This document compares three approaches.

---

## 1. LLM-Based (Current + Enhanced)

**How it works:** The LLM reads the full session transcript and identifies entities, relations, and observations using its general language understanding. Currently done as Step 2 of `/rtclose`.

**Current implementation:**
- Claude reads the generated clean dialog markdown
- Calls `create_entities()`, `create_relations()`, `add_observations()` via MCP tools
- Extraction criteria defined in `rtclose.md` prompt (entity types, kebab-case naming, project prefixes)
- No structured pipeline — entirely dependent on LLM following instructions

**Strengths:**
- Zero additional infrastructure — uses the LLM already in the conversation
- Understands context deeply — knows what was important in the session
- Handles novel concepts well — no predefined schema needed
- Can infer relations from implicit connections in text
- Works across languages (Turkish/English code-switching)

**Weaknesses:**
- Quality varies by session length — long sessions → attention dilution
- No verification — extracted entities aren't checked against any ground truth
- Duplicates happen — LLM may create `consciousness-rule` when `bilinc-kurali-1-self-reference` exists
- Relation extraction is shallow — LLM tends to default to `related_to` instead of specific types
- Token cost: full session in context + extraction prompt
- Not reproducible — same session may extract differently on re-run

**Enhancement path:**
- Dedicated extraction prompt (separate from session context) with graph state loaded
- Two-pass: first extract candidates, then deduplicate against existing graph
- Structured output (JSON mode) for consistent entity format
- Few-shot examples in prompt for relation type specificity

**Cost:** ~$0.02-0.10 per session (depends on length, model used)

---

## 2. Hybrid (LLM + NLP Pipeline)

**How it works:** A local NLP pipeline (SpaCy, stanza, or similar) handles surface-level extraction (named entities, noun phrases, co-references). The LLM then classifies, types, and connects them.

**Pipeline:**
1. **NLP pre-processing** — tokenize, POS tag, NER (if applicable)
2. **Candidate extraction** — noun phrases, repeated concepts, explicit declarations ("I decided...", "the rule is...")
3. **LLM classification** — given candidates, classify entity type, assign epistemic status, generate observations
4. **Graph deduplication** — fuzzy match against existing entities before creation

**Strengths:**
- Cheaper — NLP layer is free (local), LLM only processes candidates not full text
- More complete — NLP catches entities LLM might skip in a long session
- Reproducible — NLP stage is deterministic
- Better deduplication — fuzzy matching is systematic, not LLM judgment
- Scales better — NLP handles any session length linearly

**Weaknesses:**
- Additional dependency: SpaCy or stanza (~100MB model download)
- NER models are trained on standard entities (person, org, location) — not on philosophical concepts
- Custom NER training needed for domain-specific entities
- Turkish NLP support is limited — SpaCy's Turkish model is basic
- Two-system maintenance: NLP pipeline + LLM prompt
- Implicit relations (analogies, metaphors) still need LLM

**Implementation complexity:** Medium — SpaCy integration is straightforward, but custom patterns for concept extraction require iteration.

**Cost:** ~$0.005-0.02 per session (LLM only for classification, not full extraction)

---

## 3. Rule-Based (Pattern Matching)

**How it works:** Predefined patterns and templates extract entities from structured session text.

**Patterns:**
```
# Decision markers
"I decided..." / "Decision:" / "We chose X over Y"
→ entity(type=decision, name=slugify(X))

# Rule markers
"Rule:" / "The rule is..." / "From now on..."
→ entity(type=rule, name=slugify(content))

# Explicit graph commands
"/tag concept-name" / "create entity: ..."
→ direct entity creation

# Repeated noun phrases (TF-IDF above threshold)
→ entity(type=concept, name=slugify(phrase))

# Markdown structure
"## Key Insight" / "### Decision" / "**Important:**"
→ extract following content as observation
```

**Strengths:**
- Zero cost — no LLM, no external model
- Fastest — regex/pattern matching is microseconds
- Fully reproducible and debuggable
- Works offline
- No token overhead

**Weaknesses:**
- Only catches explicitly marked content — misses implicit knowledge
- Requires disciplined writing habits (using markers consistently)
- No relation extraction — connections between entities need manual creation or LLM
- No semantic understanding — "consciousness" and "bilinc" are unrelated to a regex
- Brittle — new patterns require new rules
- Low recall — extracts only what matches templates

**Best for:** Supplementing other approaches with high-precision extraction of explicitly marked content.

**Implementation complexity:** Low — regex patterns + session structure parsing.

---

## Comparison Matrix

| Dimension | LLM-Based | Hybrid | Rule-Based |
|-----------|-----------|--------|------------|
| **Precision** | High (when attentive) | High | Very High |
| **Recall** | Medium (attention-dependent) | High | Low |
| **Relation extraction** | Good | Good | None |
| **Cost per session** | $0.02-0.10 | $0.005-0.02 | Free |
| **Infrastructure** | None (uses existing LLM) | SpaCy/stanza | None |
| **Turkish support** | Excellent | Limited | Manual patterns |
| **Reproducibility** | Low | Medium | High |
| **Implicit knowledge** | Good | Medium | None |
| **Scale behavior** | Degrades with length | Linear | Constant |
| **Implementation effort** | Low (prompt engineering) | Medium | Low |
| **Maintenance** | Prompt updates | Pipeline + prompt | Pattern updates |

---

## Recommendation

**Phase 1 (now — publish):** Enhanced LLM-based. Improve the current approach:
- Structured extraction prompt with JSON output schema
- Two-pass: extract → deduplicate against existing graph
- Include 3-5 few-shot examples for relation type specificity
- This is what we ship. The `/rtclose` prompt gets better, no new dependencies.

**Phase 2 (post-publish):** Add rule-based layer as complement:
- Pattern matchers for explicit markers (/tag, "Decision:", "Rule:")
- TF-IDF noun phrase extraction for candidate entities
- Feed candidates to LLM for classification
- This gives the "missed entity" safety net without NLP dependencies.

**Phase 3 (if scale demands):** Evaluate hybrid:
- Only if Phase 1+2 quality metrics show gaps
- SpaCy integration for English, custom patterns for Turkish
- Justified by data, not by architecture astronautics

**Not recommended:** Full NLP pipeline as starting point. The dependency cost (SpaCy model, Turkish NER training, pipeline maintenance) isn't justified when our current entity count is ~120. Build for what you need, benchmark, then optimize.

---

## Open Questions

- What's the extraction quality baseline? We need ground truth: take 5 sessions, manually identify all entities/relations, then measure what current extraction misses.
- Should extraction run at session close only, or also mid-session for long conversations?
- How to handle entity name normalization across languages? ("consciousness" vs "bilinc" should merge or link)
- Cost ceiling: what's the maximum acceptable per-session extraction cost?
