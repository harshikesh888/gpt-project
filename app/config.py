"""Environment-driven application settings.

All configuration is read once at process start.  The app is intentionally
stateless apart from an in-memory rate limiter and response cache, so it can be
deployed to any small container host without a database.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Backends on these hosts are treated as local: they do not need an API key.
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0", "host.docker.internal"}

# Load a local .env for development without overriding real deployment
# environment variables.
load_dotenv(BASE_DIR / ".env", override=False)


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _env_int(name: str, default: int, *, minimum: int | None = None) -> int:
    raw = _env(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    if minimum is not None and value < minimum:
        return minimum
    return value


def _env_float(name: str, default: float, *, minimum: float | None = None) -> float:
    raw = _env(name)
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    if minimum is not None and value < minimum:
        return minimum
    return value


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env(name).lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _env_list(name: str) -> tuple[str, ...]:
    raw = _env(name)
    if not raw:
        return ()
    return tuple(item.strip() for item in raw.split(",") if item.strip())


@dataclass(frozen=True, slots=True)
class Settings:
    """Validated runtime configuration."""

    app_name: str = "Jarvis AI"
    app_version: str = "2.0.0"
    environment: str = "production"

    llm_api_key: str = ""
    llm_base_url: str = "http://localhost:11434/v1"
    provider: str = ""

    fast_model: str = "qwen2.5-coder:7b"
    deep_model: str = "qwen2.5-coder:7b"
    fallback_model: str = ""

    connect_timeout: float = 10.0
    read_timeout: float = 120.0
    write_timeout: float = 30.0
    pool_timeout: float = 5.0

    fast_max_tokens: int = 700
    deep_max_tokens: int = 1500
    fast_temperature: float = 0.5
    deep_temperature: float = 0.6

    max_message_chars: int = 4_000
    max_history_messages: int = 12
    max_history_chars: int = 12_000
    request_body_limit: int = 64 * 1024

    max_concurrent_streams: int = 32
    concurrency_wait_seconds: float = 2.0

    rate_limit_requests: int = 30
    rate_limit_window_seconds: int = 60
    rate_limit_max_clients: int = 20_000

    cache_enabled: bool = True
    cache_ttl_seconds: int = 900
    cache_max_entries: int = 256
    cache_max_chars: int = 6_000

    trust_proxy_headers: bool = False
    allowed_origins: tuple[str, ...] = field(default_factory=tuple)

    log_level: str = "INFO"
    docs_enabled: bool = True
    stats_enabled: bool = False

    @classmethod
    def from_env(cls) -> Settings:
        legacy_model = _env("MODEL")
        ollama_model = _env("OLLAMA_MODEL")
        default_model = "qwen2.5-coder:7b"
        fast_model = _env("FAST_MODEL") or ollama_model or legacy_model or default_model
        deep_model = _env("DEEP_MODEL") or ollama_model or legacy_model or fast_model

        return cls(
            app_name=_env("APP_NAME", "Jarvis AI") or "Jarvis AI",
            app_version=_env("APP_VERSION", "2.0.0") or "2.0.0",
            environment=_env("ENVIRONMENT", "production") or "production",
            llm_api_key=_env("LLM_API_KEY") or _env("HF_API_KEY") or _env("HF_TOKEN"),
            llm_base_url=(
                _env("LLM_BASE_URL")
                or _env("OLLAMA_BASE_URL")
                or _env("HF_BASE_URL")
                or "http://localhost:11434/v1"
            ).rstrip("/"),
            provider=_env("PROVIDER"),
            fast_model=fast_model,
            deep_model=deep_model,
            fallback_model=_env("FALLBACK_MODEL"),
            connect_timeout=_env_float("CONNECT_TIMEOUT", 10.0, minimum=1.0),
            read_timeout=_env_float("READ_TIMEOUT", 120.0, minimum=5.0),
            write_timeout=_env_float("WRITE_TIMEOUT", 30.0, minimum=1.0),
            pool_timeout=_env_float("POOL_TIMEOUT", 5.0, minimum=1.0),
            fast_max_tokens=_env_int("FAST_MAX_TOKENS", 700, minimum=64),
            deep_max_tokens=_env_int("DEEP_MAX_TOKENS", 1500, minimum=64),
            fast_temperature=_env_float("FAST_TEMPERATURE", 0.5, minimum=0.0),
            deep_temperature=_env_float("DEEP_TEMPERATURE", 0.6, minimum=0.0),
            max_message_chars=_env_int("MAX_MESSAGE_CHARS", 4_000, minimum=200),
            max_history_messages=_env_int("MAX_HISTORY_MESSAGES", 12, minimum=0),
            max_history_chars=_env_int("MAX_HISTORY_CHARS", 12_000, minimum=0),
            request_body_limit=_env_int("REQUEST_BODY_LIMIT", 64 * 1024, minimum=8 * 1024),
            max_concurrent_streams=_env_int("MAX_CONCURRENT_STREAMS", 32, minimum=1),
            concurrency_wait_seconds=_env_float(
                "CONCURRENCY_WAIT_SECONDS", 2.0, minimum=0.0
            ),
            rate_limit_requests=_env_int("RATE_LIMIT_REQUESTS", 30, minimum=0),
            rate_limit_window_seconds=_env_int(
                "RATE_LIMIT_WINDOW_SECONDS", 60, minimum=1
            ),
            rate_limit_max_clients=_env_int(
                "RATE_LIMIT_MAX_CLIENTS", 20_000, minimum=100
            ),
            cache_enabled=_env_bool("CACHE_ENABLED", True),
            cache_ttl_seconds=_env_int("CACHE_TTL_SECONDS", 900, minimum=0),
            cache_max_entries=_env_int("CACHE_MAX_ENTRIES", 256, minimum=0),
            cache_max_chars=_env_int("CACHE_MAX_CHARS", 6_000, minimum=0),
            trust_proxy_headers=_env_bool("TRUST_PROXY_HEADERS", False),
            allowed_origins=_env_list("ALLOWED_ORIGINS"),
            log_level=_env("LOG_LEVEL", "INFO").upper() or "INFO",
            docs_enabled=_env_bool("DOCS_ENABLED", True),
            stats_enabled=_env_bool("STATS_ENABLED", False),
        )

    @property
    def is_local_backend(self) -> bool:
        """True for Ollama-style servers running on this machine or its host."""
        return (urlparse(self.llm_base_url).hostname or "").lower() in _LOCAL_HOSTS

    @property
    def is_ready(self) -> bool:
        """True when the app has the minimum configuration to serve chats.

        Local backends do not require an API key; remote ones do.
        """
        if not self.fast_model:
            return False
        return self.is_local_backend or bool(self.llm_api_key)

    def model_for(self, mode: str) -> str:
        return self.deep_model if mode == "smart" else self.fast_model

    def max_tokens_for(self, mode: str) -> int:
        return self.deep_max_tokens if mode == "smart" else self.fast_max_tokens

    def temperature_for(self, mode: str) -> float:
        return self.deep_temperature if mode == "smart" else self.fast_temperature
