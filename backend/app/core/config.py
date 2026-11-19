"""All settings come from environment variables or backend/.env."""
from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "app/.env"), extra="ignore", case_sensitive=False)

    # ---- App ----
    app_name: str = "urSarthi API"
    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:3000"
    frontend_url: str = "http://localhost:3000"
    # Deployment: serve the API under a prefix (e.g. /api) and the built React app from the same server
    api_prefix: str = ""
    frontend_dist: str | None = None   # path to frontend/build; when set, the backend serves the website too

    # ---- Database (Phase-1 name `db_url` is supported) ----
    database_url: str = Field("sqlite:///./ursarthi.db", validation_alias=AliasChoices("DATABASE_URL", "db_url"))
    db_pool_size: int = 10
    db_max_overflow: int = 20

    # ---- Auth ----
    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 15
    refresh_token_days: int = 7
    reset_token_minutes: int = 30
    cookie_secure: bool = False
    cookie_samesite: Literal["lax", "strict", "none"] = "lax"

    # ---- SMTP (password reset) ----
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None

    # ---- Cache & limits ----
    redis_url: str | None = None
    rate_limit_ai_per_minute: int = 20
    rate_limit_login_per_minute: int = 10
    rate_limit_register_per_minute: int = 10

    # ---- LLM: any OpenAI-compatible API. Default = Groq (free tier). ----
    llm_api_key: str | None = Field(None, validation_alias=AliasChoices("LLM_API_KEY", "GROQ_API_KEY"))
    llm_base_url: str = "https://api.groq.com/openai/v1"
    llm_model: str = "openai/gpt-oss-20b"
    llm_reasoning_effort: str | None = "low"  # only sent to reasoning models (gpt-oss)
    llm_temperature: float = 0.4
    llm_max_tokens: int = 1800
    llm_timeout_seconds: int = 60

    # ---- Embeddings ----
    # auto -> fastembed if installed (good local model), else hash (offline, zero-dependency)
    embedding_provider: Literal["auto", "fastembed", "openai", "hash"] = "auto"
    fastembed_model: str = "BAAI/bge-small-en-v1.5"
    openai_api_key: str | None = Field(None, validation_alias=AliasChoices("OPENAI_API_KEY", "openai_api"))
    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 1536

    # ---- Vector store: auto -> pgvector if the extension exists, else numpy ----
    vector_backend: Literal["auto", "pgvector", "numpy"] = "auto"

    # ---- Retrieval tuning ----
    retrieval_top_k: int = 6
    retrieval_candidates: int = 30
    rrf_k: int = 60
    mmr_lambda: float = 0.72
    chunk_size: int = 900
    chunk_overlap: int = 120

    # ---- External services ----
    ors_api_key: str | None = Field(None, validation_alias=AliasChoices("ORS_API_KEY", "ors_api"))
    ors_base_url: str = "https://api.heigit.org/openrouteservice/v2"
    nominatim_url: str = "https://nominatim.openstreetmap.org/search"
    photon_url: str = "https://photon.komoot.io/api/"
    http_user_agent: str = "urSarthi/1.0 (student travel project)"
    knowledge_radius_m: int = 10000      # Wikipedia geosearch radius around each stop
    knowledge_cache_days: int = 30

    @field_validator("database_url")
    @classmethod
    def _normalise_db_url(cls, v: str) -> str:
        # Neon/Render/Heroku give postgres:// or postgresql:// – SQLAlchemy wants postgresql+psycopg2://
        if v.startswith("postgres://"):
            v = "postgresql://" + v[len("postgres://"):]
        if v.startswith("postgresql://"):
            v = "postgresql+psycopg2://" + v[len("postgresql://"):]
        return v

    @field_validator("api_prefix")
    @classmethod
    def _normalise_prefix(cls, v: str) -> str:
        v = v.strip().rstrip("/")
        return ("/" + v.lstrip("/")) if v else ""

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def is_postgres(self) -> bool:
        return self.database_url.startswith("postgres")

    @property
    def smtp_configured(self) -> bool:
        return all([self.smtp_host, self.smtp_username, self.smtp_password, self.smtp_from])


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    if not settings.jwt_secret or len(settings.jwt_secret) < 32:
        if settings.environment == "production":
            raise RuntimeError("JWT_SECRET must be set and contain at least 32 characters.")
        import logging
        import secrets

        logging.getLogger(__name__).warning("JWT_SECRET missing; using a temporary secret (sessions reset on restart).")
        settings.jwt_secret = secrets.token_urlsafe(48)
    return settings