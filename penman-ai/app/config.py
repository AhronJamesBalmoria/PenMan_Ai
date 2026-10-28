from pydantic_settings import BaseSettings
from dotenv import load_dotenv
import os
import sys

load_dotenv()


class Settings:
    SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
    SUPABASE_ANON_KEY: str = os.getenv("SUPABASE_ANON_KEY", "")
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    SECRET_KEY: str = os.getenv("SECRET_KEY", "dev-secret-change-me")

    SESSION_COOKIE_NAME: str = "penman_session"

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
            print("\n" + "=" * 60)
            print("❌ MISSING ENVIRONMENT VARIABLES")
            print("=" * 60)
            for key in missing:
                print(f"  • {key}")
            print("\nCopy .env.example to .env and fill in the values.")
            print("=" * 60 + "\n")
            sys.exit(1)


settings = Settings()
settings.validate()