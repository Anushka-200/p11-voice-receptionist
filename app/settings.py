"""All configuration comes from environment variables (or .env). Nothing secret lives in code."""

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://p11_app@127.0.0.1:5441/p11"
    migrator_database_url: str = "postgresql://p11_migrator@127.0.0.1:5441/p11"

    # Safety first: unless turned off, nothing leaves the machine (SMS, CRM writes, calendar writes).
    dry_run: bool = True
    # HMAC key for phone numbers: a plain hash of a phone number is brute-forceable (~10 billion US numbers).
    phone_hash_key: SecretStr = SecretStr("")
    log_level: str = "INFO"
    clinic_timezone: str = "America/Chicago"

    # --- LLM (post-call analysis and the text eval; the live call uses Vapi's own model, ADR 002) ---
    anthropic_api_key: SecretStr = SecretStr("")
    llm_default_model: str = (
        "claude-haiku-5-5"  # cheap tier; prices are verified and dated in docs/benchmarks.md
    )
    llm_fallback_model: str = "claude-sonnet-5-5"  # used when the default keeps failing (provider incident)
    llm_timeout_s: float = 30.0
    llm_prompt_caching: bool = True
    llm_max_retries: int = 3
    cost_confirm_above_usd: float = 0.50  # ask the owner before any run that may cost more

    # --- Vapi (Phase 0 spike fills these in). The webhook secret is checked on EVERY incoming request. ---
    vapi_api_key: SecretStr = SecretStr("")  # private key: used by `make sync-assistant` only
    vapi_webhook_secret: SecretStr = SecretStr("")  # empty = every request is rejected
    vapi_assistant_id: str = ""
    spike_payload_dir: str = "logs/vapi"  # Phase 0 only: raw Vapi payloads saved here to learn their shape
    public_base_url: str = ""  # the cloudflared tunnel URL, set by hand when you run `make tunnel`

    # --- Real integrations. Default = fakes. Real WRITES still obey DRY_RUN. ---
    calendar_backend: str = "fake"  # fake | google
    google_calendar_id: str = ""
    google_credentials_file: str = ""  # outside git
    crm_backend: str = "fake"  # fake | hubspot
    hubspot_token: SecretStr = SecretStr("")

    # --- Langfuse tracing; with no keys every tracing call quietly does nothing ---
    langfuse_public_key: str = ""
    langfuse_secret_key: SecretStr = SecretStr("")
    langfuse_host: str = "https://cloud.langfuse.com"


@lru_cache
def get_settings() -> Settings:
    return Settings()
