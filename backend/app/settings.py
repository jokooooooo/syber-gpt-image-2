from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_INSPIRATION_SOURCE_URLS = [
    "https://raw.githubusercontent.com/EvoLinkAI/awesome-gpt-image-2-prompts/main/README.md",
    "https://raw.githubusercontent.com/YouMind-OpenLab/awesome-gpt-image-2/main/README.md",
]


def _env_path(name: str, default: Path) -> Path:
    value = os.getenv(name)
    return Path(value).expanduser().resolve() if value else default.resolve()


def _derive_auth_base_url(provider_base_url: str) -> str:
    base_url = provider_base_url.rstrip("/")
    if base_url.endswith("/v1"):
        return base_url[:-3]
    return base_url


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    backend_dir: Path
    database_path: Path
    storage_dir: Path
    provider_base_url: str
    auth_base_url: str
    provider_usage_path: str
    image_model: str
    prompt_optimizer_model: str
    default_size: str
    default_quality: str
    image_price_1k: float
    image_price_2k: float
    image_price_4k: float
    user_name: str
    cors_origins: list[str]
    request_timeout_seconds: float
    inspiration_source_url: str
    inspiration_sync_interval_seconds: float
    inspiration_sync_on_startup: bool
    session_cookie_name: str
    guest_cookie_name: str
    session_ttl_seconds: int
    guest_ttl_seconds: int
    cookie_secure: bool
    trial_key_enabled: bool
    trial_key_quota_usd: float
    trial_key_expires_days: int
    trial_key_name_prefix: str
    trial_balance_grant_enabled: bool
    trial_balance_usd: float
    sub2api_admin_token: str
    sub2api_admin_jwt: str
    recharge_url: str
    inspiration_source_urls: list[str] | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        backend_dir = Path(__file__).resolve().parents[1]
        provider_base_url = os.getenv("SUB2API_BASE_URL", "http://127.0.0.1:9878/v1").rstrip("/")
        cors_origins = [
            origin.strip()
            for origin in os.getenv(
                "CORS_ORIGINS",
                "http://127.0.0.1:3000,http://localhost:3000,http://127.0.0.1:5173,http://localhost:5173",
            ).split(",")
            if origin.strip()
        ]
        source_urls = _split_csv(
            os.getenv(
                "INSPIRATION_SOURCE_URLS",
                os.getenv("INSPIRATION_SOURCE_URL", ",".join(DEFAULT_INSPIRATION_SOURCE_URLS)),
            )
        )
        if not source_urls:
            source_urls = DEFAULT_INSPIRATION_SOURCE_URLS
        return cls(
            backend_dir=backend_dir,
            database_path=_env_path("DATABASE_PATH", backend_dir / "data" / "app.sqlite3"),
            storage_dir=_env_path("STORAGE_DIR", backend_dir / "storage"),
            provider_base_url=provider_base_url,
            auth_base_url=os.getenv("SUB2API_AUTH_BASE_URL", _derive_auth_base_url(provider_base_url)).rstrip("/"),
            provider_usage_path=os.getenv("SUB2API_USAGE_PATH", "/v1/usage"),
            image_model=os.getenv("IMAGE_MODEL", "gpt-image-2"),
            prompt_optimizer_model=os.getenv("PROMPT_OPTIMIZER_MODEL", "gpt-5.5"),
            default_size=os.getenv("IMAGE_SIZE", "2K"),
            default_quality=os.getenv("IMAGE_QUALITY", "auto"),
            image_price_1k=float(os.getenv("IMAGE_PRICE_1K_USD", "0.134")),
            image_price_2k=float(os.getenv("IMAGE_PRICE_2K_USD", "0.201")),
            image_price_4k=float(os.getenv("IMAGE_PRICE_4K_USD", "0.268")),
            user_name=os.getenv("APP_USER_NAME", "NEON_USER_404"),
            cors_origins=cors_origins,
            request_timeout_seconds=float(os.getenv("PROVIDER_TIMEOUT_SECONDS", "300")),
            inspiration_source_url=source_urls[0],
            inspiration_sync_interval_seconds=float(os.getenv("INSPIRATION_SYNC_INTERVAL_SECONDS", "21600")),
            inspiration_sync_on_startup=os.getenv("INSPIRATION_SYNC_ON_STARTUP", "true").lower()
            not in {"0", "false", "no", "off"},
            session_cookie_name=os.getenv("SESSION_COOKIE_NAME", "cybergen_session"),
            guest_cookie_name=os.getenv("GUEST_COOKIE_NAME", "cybergen_guest"),
            session_ttl_seconds=int(os.getenv("SESSION_TTL_SECONDS", str(30 * 24 * 60 * 60))),
            guest_ttl_seconds=int(os.getenv("GUEST_TTL_SECONDS", str(365 * 24 * 60 * 60))),
            cookie_secure=_env_bool("COOKIE_SECURE", False),
            trial_key_enabled=_env_bool("TRIAL_KEY_ENABLED", True),
            trial_key_quota_usd=float(os.getenv("TRIAL_KEY_QUOTA_USD", "0")),
            trial_key_expires_days=int(os.getenv("TRIAL_KEY_EXPIRES_DAYS", os.getenv("TRIAL_KEY_EXPIRE_DAYS", "30"))),
            trial_key_name_prefix=os.getenv("TRIAL_KEY_NAME_PREFIX", "joko-image2-trial"),
            trial_balance_grant_enabled=_env_bool("TRIAL_BALANCE_GRANT_ENABLED", True),
            trial_balance_usd=float(os.getenv("TRIAL_BALANCE_USD", "2")),
            sub2api_admin_token=os.getenv("SUB2API_ADMIN_TOKEN", "").strip(),
            sub2api_admin_jwt=os.getenv("SUB2API_ADMIN_JWT", "").strip(),
            recharge_url=os.getenv("RECHARGE_URL", "https://ai.get-money.locker").strip(),
            inspiration_source_urls=source_urls,
        )

    @property
    def images_dir(self) -> Path:
        return self.storage_dir / "images"

    @property
    def uploads_dir(self) -> Path:
        return self.storage_dir / "uploads"

    @property
    def inspirations_dir(self) -> Path:
        return self.storage_dir / "inspirations"

    def ensure_directories(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        self.inspirations_dir.mkdir(parents=True, exist_ok=True)
