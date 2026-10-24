"""Application settings, loaded from the environment / .env file.

Secrets (the Anthropic API key) live here on the backend only and are never
sent to the client.
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    anthropic_api_key: str = ""
    anthropic_auth_token: str = ""   # OAuth token (e.g. from `ant auth login`)
    claude_model: str = "claude-opus-4-8"
    # Google Gemini (free tier): key from https://aistudio.google.com/apikey
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.8-flash"
    # Local Ollama (free, offline). Requires `ollama serve` + a pulled vision model.
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5vl:3b"
    # Force a specific provider: "anthropic" | "gemini" | "ollama" | "demo". Blank = auto.
    ai_provider: str = ""
    cors_origins: str = "*"
    tripfit_mock: str = ""           # "", or 1/true/on to force demo mode, 0/false/off to force real

    @property
    def cors_origin_list(self) -> list[str]:
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def has_anthropic(self) -> bool:
        return bool(self.anthropic_api_key or self.anthropic_auth_token)

    @property
    def has_gemini(self) -> bool:
        return bool(self.gemini_api_key)

    @property
    def has_credentials(self) -> bool:
        return self.has_anthropic or self.has_gemini

    def select_provider(self) -> str:
        """Pick the AI backend: "anthropic" | "gemini" | "demo".

        Priority: explicit TRIPFIT_MOCK=on → demo; explicit AI_PROVIDER wins;
        otherwise auto-detect from whichever credential is present (Anthropic
        first, then Gemini); with none, fall back to the credential-free demo.
        """
        mock = self.tripfit_mock.strip().lower()
        if mock in ("1", "true", "yes", "on"):
            return "demo"
        forced = self.ai_provider.strip().lower()
        if forced in ("anthropic", "gemini", "ollama", "demo"):
            return forced
        if self.has_anthropic:
            return "anthropic"
        if self.has_gemini:
            return "gemini"
        if mock in ("0", "false", "no", "off"):
            return "anthropic"  # live forced but no creds → surfaces a clear 503
        return "demo"

    def use_mock(self) -> bool:
        return self.select_provider() == "demo"


@lru_cache
def get_settings() -> Settings:
    return Settings()
