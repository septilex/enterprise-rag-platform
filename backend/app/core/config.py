from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATABASE_URL: str = (
        "postgresql+psycopg2://rag_admin:rag_dev_password@localhost:5432/rag_platform"
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
    DEDUP_SIMILARITY_THRESHOLD: float = 0.95
    NO_ANSWER_MESSAGE: str = (
        "I don't have enough grounded information in the selected sources to answer that."
    )

    # --- reranking slice (RET-03) ---
    RERANK_ENABLED: bool = False
    RERANK_MODEL: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    RERANK_CANDIDATE_POOL: int = 20


settings = Settings()