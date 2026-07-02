from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATABASE_URL: str = (
        "postgresql+psycopg2://rag_admin:rag_dev_password@localhost:5432/rag_platform"
    )
    OPENAI_API_KEY: str = ""


settings = Settings()