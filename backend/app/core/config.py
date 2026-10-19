from functools import lru_cache
from pathlib import Path
from pydantic import Field, AliasChoices, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from backend.app.core.envfile import resolve_env_file, clean_secret, anchor_sqlite_url

class Settings(BaseSettings):
    database_url: str = Field(default="sqlite:///./signaltrace.db", validation_alias=AliasChoices("SIGNALTRACE_DATABASE_URL", "DATABASE_URL"))
    tavily_api_key: str | None = Field(default=None, validation_alias=AliasChoices("TAVILY_API_KEY", "SIGNALTRACE_TAVILY_API_KEY"))
    openai_api_key: str | None = Field(default=None, validation_alias=AliasChoices("OPENAI_API_KEY", "SIGNALTRACE_OPENAI_API_KEY"))
    openai_base_url: str = Field(default="https://api.openai.com/v1", validation_alias=AliasChoices("OPENAI_BASE_URL", "SIGNALTRACE_OPENAI_BASE_URL"))
    openai_model: str = Field(default="free/gemini-3.8-flash", validation_alias=AliasChoices("OPENAI_MODEL", "SIGNALTRACE_OPENAI_MODEL"))
    llm_timeout_seconds: float = Field(default=60.0, validation_alias=AliasChoices("LLM_TIMEOUT_SECONDS", "SIGNALTRACE_LLM_TIMEOUT_SECONDS"))
    llm_max_retries: int = Field(default=2, validation_alias=AliasChoices("LLM_MAX_RETRIES", "SIGNALTRACE_LLM_MAX_RETRIES"))
    llm_json_mode: str = Field(default="auto", validation_alias=AliasChoices("LLM_JSON_MODE", "SIGNALTRACE_LLM_JSON_MODE"))
    search_timeout_seconds: float = Field(default=20.0, validation_alias=AliasChoices("SEARCH_TIMEOUT_SECONDS", "SIGNALTRACE_SEARCH_TIMEOUT_SECONDS"))
    search_max_retries: int = Field(default=2, validation_alias=AliasChoices("SEARCH_MAX_RETRIES", "SIGNALTRACE_SEARCH_MAX_RETRIES"))
    max_results_per_query: int = Field(default=8, validation_alias=AliasChoices("MAX_RESULTS_PER_QUERY", "SIGNALTRACE_MAX_RESULTS_PER_QUERY"))
    max_search_queries: int = Field(default=10, validation_alias=AliasChoices("MAX_SEARCH_QUERIES", "SIGNALTRACE_MAX_SEARCH_QUERIES"))
    max_pdf_bytes: int = Field(default=40_000_000, validation_alias=AliasChoices("MAX_PDF_BYTES", "SIGNALTRACE_MAX_PDF_BYTES"))
    max_pdf_pages: int = Field(default=500, validation_alias=AliasChoices("MAX_PDF_PAGES", "SIGNALTRACE_MAX_PDF_PAGES"))
    ocr_enabled: bool = Field(default=False, validation_alias=AliasChoices("OCR_ENABLED", "SIGNALTRACE_OCR_ENABLED"))
    expose_diagnostics: bool = Field(default=False, validation_alias=AliasChoices("EXPOSE_DIAGNOSTICS", "SIGNALTRACE_EXPOSE_DIAGNOSTICS"))
    frontend_origins: str = Field(default="http://localhost:8000,http://127.0.0.1:8000", validation_alias=AliasChoices("FRONTEND_ORIGINS", "SIGNALTRACE_FRONTEND_ORIGINS"))
    request_timeout_seconds: float = Field(default=15.0, validation_alias=AliasChoices("REQUEST_TIMEOUT_SECONDS", "SIGNALTRACE_REQUEST_TIMEOUT_SECONDS"))
    max_source_bytes: int = Field(default=5_000_000, validation_alias=AliasChoices("MAX_SOURCE_BYTES", "SIGNALTRACE_MAX_SOURCE_BYTES"))
    max_document_chars: int = Field(default=400_000, validation_alias=AliasChoices("MAX_DOCUMENT_CHARS", "SIGNALTRACE_MAX_DOCUMENT_CHARS"))
    max_research_sources: int = Field(default=24, validation_alias=AliasChoices("MAX_RESEARCH_SOURCES", "SIGNALTRACE_MAX_RESEARCH_SOURCES"))
    fetch_concurrency: int = Field(default=4, validation_alias=AliasChoices("FETCH_CONCURRENCY", "SIGNALTRACE_FETCH_CONCURRENCY"))
    user_agent: str = Field(default="SignalTraceResearch/0.3 (public-source research)", validation_alias=AliasChoices("USER_AGENT", "SIGNALTRACE_USER_AGENT"))
    api_token: str | None = Field(default=None, validation_alias=AliasChoices("SIGNALTRACE_API_TOKEN", "API_TOKEN"))
    rate_limit_per_minute: int = Field(default=0, validation_alias=AliasChoices("RATE_LIMIT_PER_MINUTE", "SIGNALTRACE_RATE_LIMIT_PER_MINUTE"))
    model_config = SettingsConfigDict(env_file=resolve_env_file(), env_file_encoding="utf-8", extra="ignore", populate_by_name=True, env_ignore_empty=True, validate_default=True)

    @field_validator("tavily_api_key", "openai_api_key", "api_token", mode="before")
    @classmethod
    def clean_optional_secrets(cls, value):
        return clean_secret(value)

    @field_validator("database_url", mode="after")
    @classmethod
    def stable_database_path(cls, value: str) -> str:
        return anchor_sqlite_url(value)

    @field_validator("openai_base_url", mode="before")
    @classmethod
    def trim_base_url(cls, value):
        return str(value or "https://api.openai.com/v1").strip().rstrip("/")

    @property
    def cors_origins(self) -> list[str]:
        return [x.strip() for x in self.frontend_origins.split(",") if x.strip()]

@lru_cache
def get_settings() -> Settings:
    return Settings(_env_file=resolve_env_file())
