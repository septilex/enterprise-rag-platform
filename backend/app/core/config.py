from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATABASE_URL: str = (
        "postgresql+psycopg2://<user>:<password>@localhost:5432/rag_platform"
    )
    OPENAI_API_KEY: str = ""

    QDRANT_HOST: str = "localhost"
    QDRANT_PORT: int = 6333
    QDRANT_COLLECTION: str = "rag_chunks"
    QDRANT_VECTOR_SIZE: int = 1536

    # --- generation slice ---
    OPENAI_CHAT_MODEL: str = "gpt-4o"
    CHAT_TOP_K: int = 5
    MIN_RETRIEVAL_SCORE: float = 0.2
    CONTEXT_TOKEN_BUDGET: int = 2000
    # Max number of chunks assembled into the grounded context (RET-06/RET-07).
    # The token budget is still the hard cap; this bounds citation noise.
    CHAT_MAX_CONTEXT_CHUNKS: int = 5
    DEDUP_SIMILARITY_THRESHOLD: float = 0.95
    NO_ANSWER_MESSAGE: str = (
        "I don't have enough grounded information in the selected sources to answer that."
    )

    # --- reranking slice (RET-03) ---
    RERANK_ENABLED: bool = False
    # "none" | "heuristic" (no torch) | "cross_encoder" (needs sentence-transformers)
    RERANK_STRATEGY: str = "heuristic"
    RERANK_MODEL: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    RERANK_CANDIDATE_POOL: int = 20

    # --- query transformation slice (RET-08) ---
    # "none" | "rewrite" | "hyde"
    QUERY_TRANSFORM: str = "none"

    # --- agentic multi-hop retrieval (RET-09) ---
    MULTI_HOP_ENABLED: bool = False
    MULTI_HOP_MAX_HOPS: int = 2

    # --- hybrid retrieval slice (RET-01) ---
    HYBRID_ENABLED: bool = False
    SPARSE_CANDIDATE_POOL: int = 20
    RRF_K: int = 60  # reciprocal-rank-fusion constant

    # --- caching slice (CACHE-01/03/04/05/07/08) ---
    REDIS_URL: str = "redis://localhost:6379/0"
    CACHE_ENABLED: bool = False
    EMBED_CACHE_TTL: int = 604800     # 7 days
    RETRIEVAL_CACHE_TTL: int = 300    # 5 minutes

    # --- background uploads (ING-09) ---
    # Uploads larger than this are spooled to disk and ingested by the worker
    # (bulk lane) so the browser never blocks on parse+embed of big files.
    UPLOAD_BACKGROUND_THRESHOLD_BYTES: int = 1_000_000
    UPLOAD_SPOOL_DIR: str = "var/upload_spool"

    # --- semantic response cache (CACHE-02) ---
    SEMANTIC_CACHE_ENABLED: bool = False
    SEMANTIC_CACHE_TTL: int = 3600            # 1 hour
    SEMANTIC_CACHE_THRESHOLD: float = 0.95    # cosine similarity to count as a hit
    SEMANTIC_CACHE_MAX_ENTRIES: int = 200     # per tenant+collection scan bound

    # --- tracing export (MON-01) ---
    OTEL_EXPORTER_OTLP_ENDPOINT: str = ""   # e.g. http://tempo:4318/v1/traces
    OTEL_CONSOLE_EXPORT: bool = False

    # --- query-log PII redaction (MON-04) ---
    LOG_PII_REDACTION: bool = True

    # --- scheduled ingestion (ING-03) ---
    SCHEDULER_INTERVAL: int = 60  # seconds between scheduler ticks

    # --- structured logging (MON-04) ---
    LOG_FORMAT: str = "text"  # "text" | "json"

    # --- external LLM observability (MON-09) ---
    LLM_OBS_ENABLED: bool = False
    LLM_OBS_ENDPOINT: str = ""   # Langfuse/Opik/Arize ingestion webhook
    LLM_OBS_API_KEY: str = ""

    # --- drift monitoring (MON-05) ---
    DRIFT_THRESHOLD: float = 0.3

    # --- cost attribution (MON-08) ---
    COST_PER_1K_LLM_TOKENS: float = 0.005
    COST_PER_1K_EMBED_TEXTS: float = 0.0001

    # --- auth slice (SEC-01) ---
    # Comma-separated API keys. When non-empty, all API endpoints require a
    # valid key; when empty, auth is disabled (dev/test convenience).
    API_KEYS: str = ""

    # --- CORS ---
    # Comma-separated allowed origins for browser clients. Empty -> a permissive
    # localhost dev default (Vite ports) so local dev works out of the box.
    ALLOWED_ORIGINS: str = ""

    @property
    def allowed_origins_list(self) -> list[str]:
        configured = [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]
        if configured:
            return configured
        return [
            "http://localhost:5173", "http://localhost:5174",
            "http://localhost:5175", "http://localhost:8080",
            "http://127.0.0.1:5173", "http://127.0.0.1:5175",
        ]

    # --- identity / auth mode (SEC-01/02) ---
    # "open" (dev/tests, superuser), "dev" (DB users via X-User-Email header),
    # "oidc" (SSO via bearer JWT).
    AUTH_MODE: str = "open"

    # --- OIDC / SSO (SEC-01) ---
    OIDC_ISSUER: str = ""
    OIDC_AUDIENCE: str = ""
    OIDC_JWKS_URL: str = ""            # production: verify RS256 against provider JWKS
    OIDC_DEV_SECRET: str = ""         # local/staging: HS256 shared secret
    # Auto-provision a User row on first successful SSO login (no membership).
    OIDC_AUTO_PROVISION: bool = True

    # --- RBAC slice (SEC-02) ---
    # JSON mapping api_key -> {"tenant_id": "<uuid>", "collections": ["<uuid>"]|"*"}.
    # When set, each key is scoped to a tenant and (optionally) a collection
    # allow-list; requests outside that scope are rejected with 403.
    PRINCIPALS_JSON: str = ""

    @property
    def api_key_set(self) -> set[str]:
        return {k.strip() for k in self.API_KEYS.split(",") if k.strip()}


settings = Settings()