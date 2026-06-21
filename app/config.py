"""Application configuration from environment variables."""
import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    # Telegram
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_WEBHOOK_SECRET: str = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")

    # Geteiltes Secret für proaktive Pushes aus der Coach-App (z.B. Plan-Freigabe).
    PUSH_SECRET: str = os.getenv("PUSH_SECRET", "")

    # Claude
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    CLAUDE_MODEL_DEFAULT: str = os.getenv(
        "CLAUDE_MODEL_DEFAULT", "claude-haiku-4-5-20251001"
    )
    CLAUDE_MODEL_PREMIUM: str = os.getenv("CLAUDE_MODEL_PREMIUM", "claude-sonnet-4-6")

    # Supabase
    SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
    SUPABASE_SERVICE_KEY: str = os.getenv("SUPABASE_SERVICE_KEY", "")

    # App
    APP_BASE_URL: str = os.getenv("APP_BASE_URL", "")
    DEFAULT_COACH_ID: str = os.getenv("DEFAULT_COACH_ID", "")
    TZ: str = os.getenv("TZ", "Europe/Vienna")
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    DEFAULT_COACH_ID: str = os.getenv("DEFAULT_COACH_ID", "")

    def validate(self) -> list[str]:
        """Return list of missing required env vars."""
        missing = []
        required = [
            "TELEGRAM_BOT_TOKEN",
            "ANTHROPIC_API_KEY",
            "SUPABASE_URL",
            "SUPABASE_SERVICE_KEY",
            "DEFAULT_COACH_ID",
        ]
        for key in required:
            if not getattr(self, key):
                missing.append(key)
        return missing


settings = Settings()
