"""Application configuration loaded from environment variables."""
import contextlib
import logging
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent

load_dotenv(PROJECT_ROOT / ".env")

logger = logging.getLogger(__name__)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def is_jwt_shaped(value: str) -> bool:
    """True for an anon/service-role key, which is *not* the HMAC secret.

    Both are copied from the Supabase dashboard and easy to confuse. Using a
    JWT as the verification key rejects every valid session, so callers fall
    back to remote verification instead.
    """
    return value.startswith("eyJ") and value.count(".") == 2


KNOWN_ENVS = {"development", "test", "production"}


class Settings:
    # --- environment -----------------------------------------------------
    APP_ENV: str = os.getenv("APP_ENV", "development")
    # Public origin used in auth emails. Set it in production so a spoofed
    # Host header cannot put an attacker's domain in a reset/confirm link.
    # Empty means "use request.base_url" (fine for local development).
    APP_BASE_URL: str = os.getenv("APP_BASE_URL", "").rstrip("/")

    # --- Supabase --------------------------------------------------------
    SUPABASE_URL: str = os.getenv("SUPABASE_URL", "").rstrip("/")
    SUPABASE_ANON_KEY: str = os.getenv("SUPABASE_ANON_KEY", "")
    # HS256 secret from Supabase → Settings → API. When set, sessions are
    # verified locally (no network round-trip per request).
    SUPABASE_JWT_SECRET: str = os.getenv("SUPABASE_JWT_SECRET", "")
    SUPABASE_JWT_AUDIENCE: str = os.getenv(
        "SUPABASE_JWT_AUDIENCE", "authenticated")

    # --- AI --------------------------------------------------------------
    # Structure/contextual analysis and style-metric extraction go through
    # Groq with an OpenRouter fallback. At least one key is required.
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
    OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "")
    # Groq retires models quickly; keep both names configurable so a model
    # change is a .env edit rather than a code change. ``llama3-8b-8192`` is
    # no longer offered, so gpt-oss-20b + Qwen are the current stable pair.
    GROQ_MODEL: str = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")
    GROQ_FALLBACK_MODEL: str = os.getenv(
        "GROQ_FALLBACK_MODEL", "qwen/qwen3.8-27b")
    OPENROUTER_MODEL: str = os.getenv(
        "OPENROUTER_MODEL", "google/gemma-4-26b-a4b-it:free")

    # --- sessions --------------------------------------------------------
    SESSION_COOKIE_NAME: str = "penman_session"
    REFRESH_COOKIE_NAME: str = "penman_refresh"
    REFRESH_COOKIE_MAX_AGE: int = 60 * 60 * 24 * 30
    TOKEN_REFRESH_LEEWAY: int = 60

    @property
    def COOKIE_SECURE(self) -> bool:
        """Secure cookies by default everywhere except local development."""
        override = os.getenv("COOKIE_SECURE")
        if override is not None:
            return override.strip().lower() in {"1", "true", "yes", "on"}
        return self.APP_ENV.lower() == "production"

    # --- logging ---------------------------------------------------------
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()

    # --- limits ----------------------------------------------------------
    MAX_ESSAY_CHARS: int = 15_000
    MAX_PLAIN_TEXT_CHARS: int = 100_000
    MAX_CONTENT_CHARS: int = 200_000
    MAX_TITLE_CHARS: int = 200
    MAX_UPLOAD_BYTES: int = 5 * 1024 * 1024

    def validate(self) -> None:
        """Raise a clear error if required env vars are missing."""
        # Skip validation in test environments so CI/local testing doesn't
        # require real API keys - the real keys are only needed at runtime.
        if os.getenv("APP_ENV", "development").strip().lower() == "test":
            logger.warning("Skipping env validation in test mode.")
            return

        missing = []
        if not self.SUPABASE_URL:
            missing.append("SUPABASE_URL")
        if not self.SUPABASE_ANON_KEY:
            missing.append("SUPABASE_ANON_KEY")
        if not (self.GROQ_API_KEY or self.OPENROUTER_API_KEY):
            missing.append("GROQ_API_KEY (or OPENROUTER_API_KEY)")

        if missing:
            raise RuntimeError(
                "Missing required environment variables: "
                + ", ".join(missing)
                + ". Copy .env.example to .env and fill in the values."
            )

        # Auth emails build their links from APP_BASE_URL, falling back to the
        # request Host header only in development. A missing or malformed value
        # in production would email out localhost (or an attacker's origin).
        if self.APP_BASE_URL:
            parsed = urlparse(self.APP_BASE_URL)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise RuntimeError(
                    "APP_BASE_URL must be an absolute http(s) URL, e.g. "
                    "https://penman-ai.onrender.com."
                )
        elif self.APP_ENV.strip().lower() == "production":
            raise RuntimeError(
                "APP_BASE_URL must be set in production so password-reset and "
                "confirmation emails link to your public origin instead of the "
                "request Host header."
            )

        if is_jwt_shaped(self.SUPABASE_JWT_SECRET):
            logger.error(
                "SUPABASE_JWT_SECRET looks like a JWT (anon or service-role key). "
                "Local session verification is disabled. Paste the JWT Secret "
                "from Supabase -> Project Settings -> API instead."
            )
        elif not self.SUPABASE_JWT_SECRET:
            logger.warning(
                "SUPABASE_JWT_SECRET is not set; every request verifies its "
                "session against Supabase. Set it to avoid the extra round-trip."
            )

        if self.APP_ENV.strip().lower() not in KNOWN_ENVS:
            logger.warning(
                "Unknown APP_ENV=%r; expected one of %s.",
                self.APP_ENV,
                ", ".join(sorted(KNOWN_ENVS)),
            )

        if self.APP_ENV.lower() != "production":
            logger.warning(
                "APP_ENV=%s: session cookies are not marked Secure. "
                "Set APP_ENV=production when serving over HTTPS.",
                self.APP_ENV,
            )


settings = Settings()


def configure_logging() -> None:
    """Make application ``logger.info`` calls visible in the Uvicorn terminal.

    Uvicorn only configures its own ``uvicorn.*`` loggers, so the application's
    root logger otherwise stays at Python's WARNING default and every
    ``logger.info`` in the services is silently dropped.
    """
    level = getattr(logging, settings.LOG_LEVEL, logging.INFO)
    root = logging.getLogger()

    if not root.handlers:
        stream = sys.stdout
        # Windows consoles default to cp1252, which cannot encode the emoji
        # used by some model output. Replace rather than crash.
        with contextlib.suppress(AttributeError, ValueError):
            stream.reconfigure(encoding="utf-8", errors="replace")
        logging.basicConfig(
            level=level,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
            stream=stream,
        )
    else:
        root.setLevel(level)


configure_logging()
