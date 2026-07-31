"""Configuration for RAWThink semantic search pipeline."""
import os

# Vault paths
VAULT_PATH = os.environ.get(
    "RAWTHINK_VAULT",
    os.path.join(os.path.dirname(__file__), "..", "vault"),
)
SESSIONS_GLOB = "sessions/**/*.md"
QNOTES_GLOB = "qnotes/**/*.md"

# Qdrant — server mode (Docker) preferred, embedded fallback
QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")
QDRANT_PATH = os.environ.get("QDRANT_PATH")  # None = server mode
COLLECTION_NAME = "rawthink.vault"

# Embedding — BGE-M3 via Ollama
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "bge-m3")
VECTOR_DIM = 1024
EMBED_INSTRUCTION = ""  # BGE-M3 does not require instruction prefix

# BM25 sparse state. Lives with the vault, not with the package: two vaults on
# one machine used to share a single IDF table, and a read-only install could
# not write it at all.
BM25_STATE_FILE = os.environ.get(
    "RAWTHINK_BM25_STATE",
    os.path.join(VAULT_PATH, ".bm25_state.json"),
)

# Knowledge graph
MEMORY_FILE = os.environ.get(
    "MEMORY_FILE_PATH",
    os.path.join(VAULT_PATH, "memory.jsonl"),
)

# Turkish character normalization — opt-in for Turkish users
ENABLE_TURKISH_NORMALIZATION = os.environ.get("RAWTHINK_TURKISH_NORMALIZATION", "").lower() in ("1", "true", "yes")

# ---------------------------------------------------------------------------
# Controlled vocabularies
#
# entityType answers "what epistemic role does this node play"; `domain`
# answers "what field is it in". Keeping them in separate fields is what stops
# the type list from growing once per subject area.
# ---------------------------------------------------------------------------

ENTITY_TYPES = {
    "decision",       # a choice that was made, with alternatives rejected
    "concept",        # an idea, theory, model or analogy
    "finding",        # something discovered or measured — bug, result, audit
    "rule",           # a durable constraint or pattern to follow
    "open-question",  # unresolved, waiting on evidence or a decision
    "artifact",       # a project, tool, document, feature, source
    "insight",        # a realisation that changed how something is seen
    "task",           # a unit of intended work
    "event",          # something that happened at a point in time
    "thing",          # a person, object or substance referred to by name
}

DOMAINS = {
    "software", "music", "history", "philosophy", "health",
    "writing", "neuro", "finance", "personal", "galaxy",
}

EPISTEMIC_VALUES = {
    "assertion",    # held to be true, with grounds
    "hypothesis",   # plausible, not yet verified
    "speculation",  # entertained, weakly supported
    "unknown",      # never stated — the honest default, not a claim
}
DEFAULT_EPISTEMIC = "unknown"

VISIBILITY_VALUES = {"private", "shareable"}
DEFAULT_VISIBILITY = "private"

# Controlled relation vocabulary
RELATION_TYPES = {
    "supports", "contradicts", "evolved_into", "depends_on",
    "exemplifies", "part_of", "caused_by", "enables",
    "supersedes", "related_to",
    # added from observed usage in a real vault
    "investigates", "informs", "uses",
}

# Near-synonyms seen in practice, folded into the canonical form rather than
# rejected. Rejecting a reasonable synonym trains callers to fight the
# vocabulary; folding it keeps the graph queryable without the friction.
RELATION_ALIASES = {
    "connected_to": "related_to",
    "aspect_of": "part_of",
    "demonstrates": "exemplifies",
    "extends": "evolved_into",
    "instance_of": "exemplifies",
    "supported_by": "supports",
    "informed_by": "informs",
    "used_by": "uses",
    "validated_by": "supports",
    "validates": "supports",
    "solves": "enables",
    "solved_by": "enables",
    "requires": "depends_on",
    "constrained_by": "depends_on",
    "part": "part_of",
    "parcasi": "part_of",
    "icerir": "part_of",
    "arastirir": "investigates",
    "bilgilendirir": "informs",
    "etkiler": "related_to",
}

# Activation decay — half-life ~23 days
ACTIVATION_DECAY_LAMBDA = 0.03

# Embedding cache (Ollama fallback)
EMBEDDING_CACHE_SIZE = 1000

# Metadata
DOMAIN = "thoughts"
