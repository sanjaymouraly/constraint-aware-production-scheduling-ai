"""Application configuration (environment driven, validated at startup)."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent


def default_worker_count() -> int:
    """Sensible default number of CP-SAT search workers for this machine.

    The solver is CPU bound and scales well across cores, but it should never
    take every core of the host away from the API process.  Two workers are
    kept free for the web server and the database.
    """
    cpu = os.cpu_count() or 2
    return max(2, min(8, cpu - 2))


class Settings(BaseSettings):
    """Central settings object.

    Every value can be overridden through environment variables (or a `.env` file
    in the repository root).  Secrets never reach the browser bundle: the frontend
    only ever talks to this backend, which is the only component holding
    provider credentials.
    """

    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Application ---------------------------------------------------
    app_name: str = "Intelligent Production Scheduling AI"
    environment: str = Field(default="development")
    debug: bool = Field(default=False)
    api_prefix: str = "/api"
    cors_origins: str = Field(
        default="http://localhost:5173,http://127.0.0.1:5173,http://localhost:4173"
    )

    # --- Database ------------------------------------------------------
    database_url: str = Field(default=f"sqlite:///{REPO_ROOT / 'data' / 'app.db'}")
    db_echo: bool = Field(default=False)
    db_pool_size: int = Field(default=5)
    db_max_overflow: int = Field(default=10)
    #: Directory that holds the writable state (SQLite file, uploads).  A
    #: relative SQLite path in ``DATABASE_URL`` is resolved against it, so the
    #: database never depends on the working directory.  The container image
    #: sets it to ``/app/data``.
    data_dir: Path = Field(default=REPO_ROOT / "data")

    # --- Security ------------------------------------------------------
    secret_key: str = Field(default="dev-insecure-change-me-in-production")
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = Field(default=720)
    bootstrap_admin_username: str = "admin"
    bootstrap_admin_password: str = "admin"
    bootstrap_admin_email: str = "admin@example.com"
    rate_limit_enabled: bool = Field(default=True)
    rate_limit_requests: int = Field(default=240)
    rate_limit_window_seconds: int = Field(default=60)

    # --- Scheduling ----------------------------------------------------
    default_horizon_days: int = Field(default=14)
    default_time_limit_seconds: int = Field(default=20)
    max_time_limit_seconds: int = Field(default=600)
    solver_workers: int = Field(default_factory=default_worker_count)
    solver_random_seed: int = Field(default=42)
    max_concurrent_optimizations: int = Field(default=2)
    job_retention_hours: int = Field(default=48)
    #: Fixed instant used as "now" when planning.  When it is set, the demo
    #: factory, the planning horizon and the disruption events all use this
    #: instant, which makes every benchmark result reproducible.  Leave it
    #: empty for a demo that always shows the current date.
    planning_reference: datetime | None = None

    # --- Uploads -------------------------------------------------------
    max_upload_bytes: int = Field(default=5 * 1024 * 1024)
    upload_dir: Path | None = Field(default=None)

    # --- AI ------------------------------------------------------------
    ai_enabled: bool = Field(default=True)
    ai_default_provider: str = "mock"
    ai_request_timeout_seconds: float = Field(default=45.0)
    ai_max_tokens: int = Field(default=2000)
    ai_temperature: float = Field(default=0.2)
    ai_max_retries: int = Field(default=2)
    ai_monthly_budget_usd: float = Field(default=10.0)
    ai_spend_limit_usd: float = Field(default=25.0)

    # --- Observability -------------------------------------------------
    log_level: str = "INFO"
    log_json: bool = Field(default=False)
    metrics_enabled: bool = Field(default=True)

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: str | list[str] | None) -> str:
        if value is None:
            return ""
        if isinstance(value, list):
            return ",".join(value)
        return value

    @field_validator("log_level")
    @classmethod
    def _upper(cls, value: str) -> str:
        return value.upper()

    @model_validator(mode="after")
    def _resolve_paths(self) -> Settings:
        """Make every path independent of the working directory.

        A **relative** SQLite path in ``DATABASE_URL`` is resolved inside
        :attr:`data_dir` (the writable state directory), so
        ``DATABASE_URL=sqlite:///app.db`` always means "``app.db`` in the data
        directory" — on the host that is ``<repo>/data/app.db``, in the
        container it is ``/app/data/app.db``.  An **absolute** path is used
        unchanged.  Without this rule the database would depend on the working
        directory and could even be created outside the data directory.
        """
        if not self.data_dir.is_absolute():
            self.data_dir = (REPO_ROOT / self.data_dir).resolve()
        if self.upload_dir is None:
            self.upload_dir = self.data_dir / "uploads"
        elif not self.upload_dir.is_absolute():
            self.upload_dir = (REPO_ROOT / self.upload_dir).resolve()

        prefix = "sqlite:///"
        if self.database_url.startswith(prefix):
            raw = self.database_url[len(prefix) :]
            if raw and raw != ":memory:" and not Path(raw).is_absolute():
                if raw.startswith("./") or raw.startswith("../"):
                    raw = raw[2:]
                self.database_url = prefix + (self.data_dir / raw).as_posix()
        return self

    @model_validator(mode="after")
    def _require_production_secrets(self) -> Settings:
        """Refuse unsafe development credentials in a production environment."""
        if self.is_production:
            # The literals below are the *development* defaults that this
            # validator rejects; they are not usable credentials.
            if (
                len(self.secret_key.encode("utf-8")) < 32
                or self.secret_key == "dev-insecure-change-me-in-production"  # nosec B105
            ):
                raise ValueError("Production SECRET_KEY must be unique and at least 32 bytes.")
            if (
                len(self.bootstrap_admin_password) < 16 or self.bootstrap_admin_password == "admin"  # nosec B105
            ):
                raise ValueError(
                    "Production BOOTSTRAP_ADMIN_PASSWORD must be unique and at least 16 characters."
                )
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"production", "prod"}

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if self.is_sqlite:
            db_path = self.database_url.replace("sqlite:///", "")
            if db_path and db_path != ":memory:":
                Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        if self.upload_dir is not None:
            self.upload_dir.mkdir(parents=True, exist_ok=True)

    def safe_summary(self) -> dict[str, object]:
        """Configuration summary safe to log (never contains secrets)."""
        return {
            "environment": self.environment,
            "database": "sqlite" if self.is_sqlite else "postgresql",
            "ai_enabled": self.ai_enabled,
            "ai_default_provider": self.ai_default_provider,
            "solver_workers": self.solver_workers,
            "default_horizon_days": self.default_horizon_days,
            "cors_origins": self.cors_origin_list,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_directories()
    return settings


settings = get_settings()


def planning_now() -> datetime:
    """The instant the planner treats as "now".

    Normally this is the current wall-clock time.  When
    ``PLANNING_REFERENCE`` (or :attr:`Settings.planning_reference`) is set, the
    fixed instant is used instead, which makes the demo factory, the planning
    horizon and the disruption events fully deterministic.  That is what the
    reproducible benchmarks in ``docs/benchmarks/`` are measured with.
    """
    reference = settings.planning_reference
    if reference is not None:
        if reference.tzinfo is None:
            return reference.replace(tzinfo=UTC)
        return reference.astimezone(UTC)
    return datetime.now(UTC).replace(second=0, microsecond=0)
