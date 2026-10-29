"""Application configuration loaded from environment variables."""
import logging
import os
from pathlib import Path

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


def _env_list(name: str, default: str) -> list[str]:
    raw = os.getenv(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


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

    # --- Supabase --------------------------------------------------------
    SUPABASE_URL: str = os.getenv("SUPABASE_URL", "").rstrip("/")
    SUPABASE_ANON_KEY: str = os.getenv("SUPABASE_ANON_KEY", "")
    # HS256 secret from Supabase → Settings → API. When set, sessions are
    # verified locally (no network round-trip per request).
    SUPABASE_JWT_SECRET: str = os.getenv("SUPABASE_JWT_SECRET", "")
    SUPABASE_JWT_AUDIENCE: str = os.getenv("SUPABASE_JWT_AUDIENCE", "authenticated")

    # --- AI --------------------------------------------------------------
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODELS: list[str] = _env_list(
        "GEMINI_MODELS",
        "gemini-3.8-flash,gemini-3.7-flash,gemini-3.6-flash",
    )
    GEMINI_REQUEST_TIMEOUT: float = float(os.getenv("GEMINI_REQUEST_TIMEOUT", "45"))
    GEMINI_TOTAL_TIMEOUT: float = float(os.getenv("GEMINI_TOTAL_TIMEOUT", "75"))

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

    # --- limits ----------------------------------------------------------
    MAX_ESSAY_CHARS: int = 15_000
    MAX_PLAIN_TEXT_CHARS: int = 100_000
    MAX_CONTENT_CHARS: int = 200_000
    MAX_TITLE_CHARS: int = 200
    MAX_UPLOAD_BYTES: int = 5 * 1024 * 1024

    def validate(self) -> None:
        """Raise a clear error if required env vars are missing."""
        missing = []
        if not self.SUPABASE_URL:
            missing.append("SUPABASE_URL")
        if not self.SUPABASE_ANON_KEY:
            missing.append("SUPABASE_ANON_KEY")
        if not self.GEMINI_API_KEY:
            missing.append("GEMINI_API_KEY")

        if missing:
            raise RuntimeError(
                "Missing required environment variables: "
                + ", ".join(missing)
                + ". Copy .env.example to .env and fill in the values."
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
