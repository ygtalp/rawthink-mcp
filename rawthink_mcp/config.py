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

# Knowledge graph
MEMORY_FILE = os.environ.get(
    "MEMORY_FILE_PATH",
    os.path.join(VAULT_PATH, "memory.jsonl"),
)

# Turkish character normalization — opt-in for Turkish users
ENABLE_TURKISH_NORMALIZATION = os.environ.get("RAWTHINK_TURKISH_NORMALIZATION", "").lower() in ("1", "true", "yes")

# Controlled relation vocabulary
RELATION_TYPES = {
    "supports", "contradicts", "evolved_into", "depends_on",
    "exemplifies", "part_of", "caused_by", "enables",
    "supersedes", "related_to",
}

# Activation decay — half-life ~23 days
ACTIVATION_DECAY_LAMBDA = 0.03

# Embedding cache (Ollama fallback)
EMBEDDING_CACHE_SIZE = 1000

# Metadata
DOMAIN = "thoughts"
