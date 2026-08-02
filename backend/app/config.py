"""App configuration.

Everything flows from environment variables (see .env.example). Fails fast
with a readable message when a required variable is missing.
"""

import sys
from functools import lru_cache

from pydantic import AliasChoices, Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    # Read-only Airtable PAT. Empty is allowed at startup so the API can run
    # without it; the mirror script checks it and exits with a clear message.
    airtable_token: str = ""
    airtable_base_id: str
    airtable_payments_table: str
    environment: str = "local"
    # Where console-captured check photos land (compose mounts a volume here;
    # Azure App Service persists /home, so prod uses /home/data/uploads).
    # Canonical env name is UPLOADS_DIR (deploy plan §1.2); UPLOAD_DIR is still
    # accepted so existing local .env files and the test suite keep working.
    upload_dir: str = Field(
        default="/data/uploads",
        validation_alias=AliasChoices("UPLOADS_DIR", "UPLOAD_DIR"),
    )
    # Absolute path to the built React bundle (frontend/dist) that FastAPI
    # serves in production. Empty in local dev, where Vite serves the frontend
    # on its own origin and this app is API-only.
    frontend_dist: str = ""
    # Stage 2 auth: OAuth client id (Google Cloud Console) and the secret
    # that signs the emg_session cookie. Both required — fail fast.
    google_client_id: str
    session_secret: str
    # Stage 3: SECOND Airtable PAT with data.records:write, used ONLY by the
    # edit write-through. The mirror keeps the read-only token — a leaked
    # mirror token still cannot modify Airtable (least privilege).
    airtable_write_token: str
    # Push mechanics slice (STAGE_PUSH_MECHANICS_SLICE.md §2): VAPID keypair for
    # Web Push. DELIBERATELY optional at startup — like airtable_token above, a
    # missing push key must not take down the whole console; the push endpoints
    # return a clear "push not configured" instead (the plan asked for fail-fast;
    # this is a blast-radius deviation, flagged to the owner). Generate with
    # `python -m app.scripts.generate_vapid_keys`.
    vapid_public_key: str = ""
    vapid_private_key: str = ""
    vapid_subject: str = "mailto:owner@example.com"
    # Decision flow (STAGE_DECISION_FLOW_BUILD_PLAN.md §2). The shared secret
    # authenticates n8n ⇄ console in BOTH directions and is REQUIRED (fail-fast):
    # hooks must never be reachable unauthenticated, so there is no safe default.
    pilot_hook_secret: str
    # Optional: empty = the outbound check-submitted trigger is disabled (dev
    # without n8n keeps working).
    n8n_pilot_webhook_url: str = ""
    # Comma-separated emails; question/resolution push fan-out goes to this
    # list ∩ roles admin/manager. Empty = no pilot pushes.
    pilot_push_emails: str = ""
    # Jobs directory sync (slab deliveries stage): the bridge's cached job
    # list feeds the typeahead picker. Empty key = sync disabled (dev/tests).
    bridge_base_url: str = "https://bridge.emgcheckbot.us"
    bridge_console_key: str = ""
    jobs_sync_minutes: int = 10
    # Slab workflow endpoints (empty = disabled, dev without n8n keeps working):
    # ingest webhook (photo -> OCR) and the completion webhook (assignments ->
    # Moraware notes + Drive move + final).
    n8n_slab_webhook_url: str = ""
    n8n_slab_decision_url: str = ""
    # Slab scans chapter: direct Claude vision fallback for labels whose QR
    # couldn't be decoded client-side. Empty = OCR endpoint disabled.
    anthropic_api_key: str = ""
    anthropic_ocr_model: str = "claude-haiku-4-5-20251001"

    @property
    def pilot_push_email_list(self) -> list[str]:
        return [e.strip().lower() for e in self.pilot_push_emails.split(",") if e.strip()]


@lru_cache
def get_settings() -> Settings:
    try:
        return Settings()
    except ValidationError as exc:
        missing = ", ".join(
            ".".join(str(part) for part in err["loc"]).upper() for err in exc.errors()
        )
        sys.exit(
            "Configuration error — missing or invalid environment variables: "
            f"{missing}. Copy .env.example to .env and fill in the values."
        )
