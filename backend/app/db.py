from __future__ import annotations

import json
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from .settings import DEFAULT_INSPIRATION_SOURCE_URLS, Settings


LEGACY_OWNER_ID = "legacy:default"
DEFAULT_SITE_LOCALE = "zh-CN"
USER_GALLERY_SOURCE_URL = "joko-image://user-gallery"
USER_GALLERY_SECTION = "用户作品"
DEFAULT_ANNOUNCEMENT_TITLE = "欢迎来到 JokoAI 图像系统"
DEFAULT_ANNOUNCEMENT_BODY = """欢迎使用 JokoAI 图像生态系统。

站主联系方式：
QQ：935764227
Telegram：https://t.me/jokoacoount

中转站 / 充值站点：
https://ai.get-money.locker

如需充值、额度支持或账号协助，请通过以上方式联系。"""


def default_inspiration_sources(settings: Settings | None = None) -> list[str]:
    if settings and settings.inspiration_source_urls:
        return settings.inspiration_source_urls
    if settings and settings.inspiration_source_url:
        return [settings.inspiration_source_url]
    return list(DEFAULT_INSPIRATION_SOURCE_URLS)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def utc_after(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).replace(microsecond=0).isoformat()


class Database:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init(self, settings: Settings) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS owner_config (
                    owner_id TEXT PRIMARY KEY,
                    api_key TEXT NOT NULL DEFAULT '',
                    managed_api_key TEXT NOT NULL DEFAULT '',
                    base_url TEXT NOT NULL,
                    usage_path TEXT NOT NULL,
                    model TEXT NOT NULL,
                    default_size TEXT NOT NULL,
                    default_quality TEXT NOT NULL,
                    user_name TEXT NOT NULL,
                    managed_by_auth INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS image_history (
                    id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL DEFAULT 'legacy:default',
                    task_id TEXT,
                    batch_index INTEGER NOT NULL DEFAULT 0,
                    mode TEXT NOT NULL CHECK (mode IN ('generate', 'edit')),
                    prompt TEXT NOT NULL,
                    model TEXT NOT NULL,
                    size TEXT NOT NULL,
                    aspect_ratio TEXT NOT NULL DEFAULT '',
                    quality TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('succeeded', 'failed')),
                    image_url TEXT,
                    image_path TEXT,
                    input_image_url TEXT,
                    input_image_path TEXT,
                    revised_prompt TEXT,
                    usage_json TEXT,
                    provider_response_json TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS image_tasks (
                    id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    mode TEXT NOT NULL CHECK (mode IN ('generate', 'edit')),
                    prompt TEXT NOT NULL,
                    model TEXT NOT NULL,
                    size TEXT NOT NULL,
                    aspect_ratio TEXT NOT NULL DEFAULT '',
                    quality TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
                    request_json TEXT,
                    input_image_url TEXT,
                    input_image_path TEXT,
                    result_history_ids_json TEXT,
                    result_json TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS ledger_entries (
                    id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL DEFAULT 'legacy:default',
                    event_type TEXT NOT NULL,
                    amount REAL NOT NULL DEFAULT 0,
                    currency TEXT NOT NULL DEFAULT 'USD',
                    description TEXT NOT NULL,
                    history_id TEXT,
                    metadata_json TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(history_id) REFERENCES image_history(id) ON DELETE SET NULL
                );

                CREATE TABLE IF NOT EXISTS user_sessions (
                    id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    sub2api_user_id INTEGER NOT NULL,
                    email TEXT NOT NULL,
                    username TEXT NOT NULL DEFAULT '',
                    role TEXT NOT NULL DEFAULT 'user',
                    access_token TEXT NOT NULL DEFAULT '',
                    refresh_token TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    user_agent TEXT,
                    ip_address TEXT
                );

                CREATE TABLE IF NOT EXISTS site_settings (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    default_locale TEXT NOT NULL DEFAULT 'zh-CN',
                    announcement_enabled INTEGER NOT NULL DEFAULT 1,
                    announcement_title TEXT NOT NULL DEFAULT '',
                    announcement_body TEXT NOT NULL DEFAULT '',
                    announcement_updated_at TEXT,
                    inspiration_sources_json TEXT NOT NULL DEFAULT '[]',
                    provider_base_url TEXT NOT NULL DEFAULT '',
                    auth_base_url TEXT NOT NULL DEFAULT '',
                    sub2api_admin_token TEXT NOT NULL DEFAULT '',
                    sub2api_admin_jwt TEXT NOT NULL DEFAULT '',
                    recharge_url TEXT NOT NULL DEFAULT '',
                    trial_balance_usd REAL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS inspiration_prompts (
                    id TEXT PRIMARY KEY,
                    source_url TEXT NOT NULL,
                    source_item_id TEXT NOT NULL,
                    section TEXT NOT NULL,
                    title TEXT NOT NULL,
                    author TEXT,
                    prompt TEXT NOT NULL,
                    image_url TEXT,
                    source_link TEXT,
                    raw_json TEXT,
                    synced_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(source_url, source_item_id)
                );

                CREATE TABLE IF NOT EXISTS inspiration_favorites (
                    owner_id TEXT NOT NULL,
                    inspiration_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(owner_id, inspiration_id),
                    FOREIGN KEY(inspiration_id) REFERENCES inspiration_prompts(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS trial_grants (
                    owner_id TEXT PRIMARY KEY,
                    sub2api_user_id INTEGER NOT NULL UNIQUE,
                    email TEXT NOT NULL DEFAULT '',
                    key_id TEXT,
                    key_hint TEXT NOT NULL DEFAULT '',
                    quota_usd REAL NOT NULL DEFAULT 0,
                    balance_granted_usd REAL NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'pending',
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_owner_config_managed ON owner_config(managed_by_auth);
                CREATE INDEX IF NOT EXISTS idx_user_sessions_owner_id ON user_sessions(owner_id);
                CREATE INDEX IF NOT EXISTS idx_user_sessions_expires_at ON user_sessions(expires_at);
                CREATE INDEX IF NOT EXISTS idx_image_tasks_owner_created_at ON image_tasks(owner_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_image_tasks_status_updated_at ON image_tasks(status, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_inspiration_prompts_synced_at ON inspiration_prompts(synced_at DESC);
                CREATE INDEX IF NOT EXISTS idx_inspiration_prompts_section ON inspiration_prompts(section);
                CREATE INDEX IF NOT EXISTS idx_inspiration_favorites_owner_created_at ON inspiration_favorites(owner_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_trial_grants_sub2api_user_id ON trial_grants(sub2api_user_id);
                """
            )
            self._migrate_legacy_schema(conn, settings)
            conn.executescript(
                """
                CREATE INDEX IF NOT EXISTS idx_image_history_owner_created_at ON image_history(owner_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_image_history_owner_task_id ON image_history(owner_id, task_id);
                CREATE INDEX IF NOT EXISTS idx_ledger_entries_owner_created_at ON ledger_entries(owner_id, created_at DESC);
                """
            )

    def _migrate_legacy_schema(self, conn: sqlite3.Connection, settings: Settings) -> None:
        owner_config_columns = _table_columns(conn, "owner_config")
        if "managed_api_key" not in owner_config_columns:
            conn.execute("ALTER TABLE owner_config ADD COLUMN managed_api_key TEXT NOT NULL DEFAULT ''")

        session_columns = _table_columns(conn, "user_sessions")
        if session_columns and "role" not in session_columns:
            conn.execute("ALTER TABLE user_sessions ADD COLUMN role TEXT NOT NULL DEFAULT 'user'")
        if session_columns and "access_token" not in session_columns:
            conn.execute("ALTER TABLE user_sessions ADD COLUMN access_token TEXT NOT NULL DEFAULT ''")
        if session_columns and "refresh_token" not in session_columns:
            conn.execute("ALTER TABLE user_sessions ADD COLUMN refresh_token TEXT NOT NULL DEFAULT ''")

        image_columns = _table_columns(conn, "image_history")
        if "owner_id" not in image_columns:
            conn.execute(
                f"ALTER TABLE image_history ADD COLUMN owner_id TEXT NOT NULL DEFAULT '{LEGACY_OWNER_ID}'"
            )
        if image_columns and "aspect_ratio" not in image_columns:
            conn.execute("ALTER TABLE image_history ADD COLUMN aspect_ratio TEXT NOT NULL DEFAULT ''")
        if image_columns and "task_id" not in image_columns:
            conn.execute("ALTER TABLE image_history ADD COLUMN task_id TEXT")
        if image_columns and "batch_index" not in image_columns:
            conn.execute("ALTER TABLE image_history ADD COLUMN batch_index INTEGER NOT NULL DEFAULT 0")

        task_columns = _table_columns(conn, "image_tasks")
        if task_columns and "aspect_ratio" not in task_columns:
            conn.execute("ALTER TABLE image_tasks ADD COLUMN aspect_ratio TEXT NOT NULL DEFAULT ''")

        ledger_columns = _table_columns(conn, "ledger_entries")
        if "owner_id" not in ledger_columns:
            conn.execute(
                f"ALTER TABLE ledger_entries ADD COLUMN owner_id TEXT NOT NULL DEFAULT '{LEGACY_OWNER_ID}'"
            )

        site_settings_columns = _table_columns(conn, "site_settings")
        if "inspiration_sources_json" not in site_settings_columns:
            conn.execute("ALTER TABLE site_settings ADD COLUMN inspiration_sources_json TEXT NOT NULL DEFAULT '[]'")
        if "provider_base_url" not in site_settings_columns:
            conn.execute("ALTER TABLE site_settings ADD COLUMN provider_base_url TEXT NOT NULL DEFAULT ''")
        if "auth_base_url" not in site_settings_columns:
            conn.execute("ALTER TABLE site_settings ADD COLUMN auth_base_url TEXT NOT NULL DEFAULT ''")
        if "sub2api_admin_token" not in site_settings_columns:
            conn.execute("ALTER TABLE site_settings ADD COLUMN sub2api_admin_token TEXT NOT NULL DEFAULT ''")
        if "sub2api_admin_jwt" not in site_settings_columns:
            conn.execute("ALTER TABLE site_settings ADD COLUMN sub2api_admin_jwt TEXT NOT NULL DEFAULT ''")
        if "recharge_url" not in site_settings_columns:
            conn.execute("ALTER TABLE site_settings ADD COLUMN recharge_url TEXT NOT NULL DEFAULT ''")
        if "trial_balance_usd" not in site_settings_columns:
            conn.execute("ALTER TABLE site_settings ADD COLUMN trial_balance_usd REAL")

        self._ensure_site_settings(conn, settings)

        if self._owner_config_exists(conn, LEGACY_OWNER_ID):
            return

        if not _table_exists(conn, "app_config"):
            return

        row = conn.execute("SELECT * FROM app_config WHERE id = 1").fetchone()
        if row is None:
            return

        self._insert_owner_config(
            conn,
            LEGACY_OWNER_ID,
            settings,
            {
                "api_key": row["api_key"],
                "managed_api_key": "",
                "base_url": row["base_url"],
                "usage_path": row["usage_path"],
                "model": row["model"],
                "default_size": row["default_size"],
                "default_quality": row["default_quality"],
                "user_name": row["user_name"],
                "managed_by_auth": 0,
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            },
        )

    def _ensure_site_settings(self, conn: sqlite3.Connection, settings: Settings | None = None) -> None:
        row = conn.execute("SELECT * FROM site_settings WHERE id = 1").fetchone()
        now = utc_now()
        sources_json = _json_or_none(default_inspiration_sources(settings))
        if row is None:
            conn.execute(
                """
                INSERT INTO site_settings (
                    id, default_locale, announcement_enabled, announcement_title,
                    announcement_body, announcement_updated_at, inspiration_sources_json,
                    created_at, updated_at
                )
                VALUES (1, ?, 1, ?, ?, ?, ?, ?, ?)
                """,
                (
                    DEFAULT_SITE_LOCALE,
                    DEFAULT_ANNOUNCEMENT_TITLE,
                    DEFAULT_ANNOUNCEMENT_BODY,
                    now,
                    sources_json,
                    now,
                    now,
                ),
            )
            return

        needs_default_announcement = (
            not str(row["announcement_title"] or "").strip()
            and not str(row["announcement_body"] or "").strip()
            and int(row["announcement_enabled"] or 0) == 0
            and row["announcement_updated_at"] == row["created_at"]
        )
        updates: dict[str, Any] = {}
        if not str(row["default_locale"] or "").strip():
            updates["default_locale"] = DEFAULT_SITE_LOCALE
        if needs_default_announcement:
            updates["announcement_enabled"] = 1
            updates["announcement_title"] = DEFAULT_ANNOUNCEMENT_TITLE
            updates["announcement_body"] = DEFAULT_ANNOUNCEMENT_BODY
            updates["announcement_updated_at"] = now
        if not _json_load(row["inspiration_sources_json"]):
            updates["inspiration_sources_json"] = sources_json
        if updates:
            updates["updated_at"] = now
            assignments = ", ".join(f"{key} = ?" for key in updates)
            values = list(updates.values())
            values.append(1)
            conn.execute(f"UPDATE site_settings SET {assignments} WHERE id = ?", values)

    def _insert_owner_config(
        self,
        conn: sqlite3.Connection,
        owner_id: str,
        settings: Settings,
        overrides: dict[str, Any] | None = None,
    ) -> None:
        now = utc_now()
        values = {
            "owner_id": owner_id,
            "api_key": "",
            "managed_api_key": "",
            "base_url": settings.provider_base_url,
            "usage_path": settings.provider_usage_path,
            "model": settings.image_model,
            "default_size": settings.default_size,
            "default_quality": settings.default_quality,
            "user_name": settings.user_name,
            "managed_by_auth": 0,
            "created_at": now,
            "updated_at": now,
        }
        if overrides:
            values.update({key: value for key, value in overrides.items() if value is not None})
        conn.execute(
            """
            INSERT INTO owner_config (
                owner_id, api_key, managed_api_key, base_url, usage_path, model, default_size,
                default_quality, user_name, managed_by_auth, created_at, updated_at
            )
            VALUES (
                :owner_id, :api_key, :managed_api_key, :base_url, :usage_path, :model, :default_size,
                :default_quality, :user_name, :managed_by_auth, :created_at, :updated_at
            )
            """,
            values,
        )

    def _owner_config_exists(self, conn: sqlite3.Connection, owner_id: str) -> bool:
        row = conn.execute("SELECT owner_id FROM owner_config WHERE owner_id = ?", (owner_id,)).fetchone()
        return row is not None

    def get_config(self, owner_id: str, settings: Settings, user_name: str | None = None) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM owner_config WHERE owner_id = ?", (owner_id,)).fetchone()
            if row is None:
                self._insert_owner_config(
                    conn,
                    owner_id,
                    settings,
                    {"user_name": user_name or settings.user_name},
                )
                row = conn.execute("SELECT * FROM owner_config WHERE owner_id = ?", (owner_id,)).fetchone()
            elif user_name and row["managed_by_auth"] and row["user_name"] != user_name:
                conn.execute(
                    "UPDATE owner_config SET user_name = ?, updated_at = ? WHERE owner_id = ?",
                    (user_name, utc_now(), owner_id),
                )
                row = conn.execute("SELECT * FROM owner_config WHERE owner_id = ?", (owner_id,)).fetchone()
            if row is None:
                raise RuntimeError("owner_config was not initialized")
            config = _config_row(row)
            site_row = conn.execute("SELECT provider_base_url FROM site_settings WHERE id = 1").fetchone()
            provider_base_url = str(site_row["provider_base_url"] or "").strip() if site_row else ""
            config["base_url"] = (provider_base_url or settings.provider_base_url).rstrip("/")
            return config

    def update_config(self, owner_id: str, settings: Settings, payload: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "api_key",
            "managed_api_key",
            "base_url",
            "usage_path",
            "model",
            "default_size",
            "default_quality",
            "user_name",
            "managed_by_auth",
        }
        updates = {key: value for key, value in payload.items() if key in allowed and value is not None}
        if not updates:
            return self.get_config(owner_id, settings)

        with self.connect() as conn:
            if not self._owner_config_exists(conn, owner_id):
                self._insert_owner_config(conn, owner_id, settings)
            updates["updated_at"] = utc_now()
            assignments = ", ".join(f"{key} = ?" for key in updates)
            values = list(updates.values())
            values.append(owner_id)
            conn.execute(f"UPDATE owner_config SET {assignments} WHERE owner_id = ?", values)
        return self.get_config(owner_id, settings)

    def get_site_settings(self) -> dict[str, Any]:
        with self.connect() as conn:
            self._ensure_site_settings(conn)
            row = conn.execute("SELECT * FROM site_settings WHERE id = 1").fetchone()
            if row is None:
                raise RuntimeError("site_settings was not initialized")
            return _site_settings_row(row)

    def update_site_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "default_locale",
            "announcement_enabled",
            "announcement_title",
            "announcement_body",
            "announcement_updated_at",
            "inspiration_sources_json",
            "provider_base_url",
            "auth_base_url",
            "sub2api_admin_token",
            "sub2api_admin_jwt",
            "recharge_url",
            "trial_balance_usd",
        }
        if "inspiration_sources" in payload:
            payload = {
                **payload,
                "inspiration_sources_json": _json_or_none(payload.get("inspiration_sources") or []),
            }
        updates = {key: value for key, value in payload.items() if key in allowed and value is not None}
        if not updates:
            return self.get_site_settings()

        with self.connect() as conn:
            self._ensure_site_settings(conn)
            if any(key in updates for key in {"announcement_enabled", "announcement_title", "announcement_body"}):
                updates["announcement_updated_at"] = utc_now()
            updates["updated_at"] = utc_now()
            assignments = ", ".join(f"{key} = ?" for key in updates)
            values = list(updates.values())
            values.append(1)
            conn.execute(f"UPDATE site_settings SET {assignments} WHERE id = ?", values)
        return self.get_site_settings()

    def apply_managed_config(
        self,
        owner_id: str,
        settings: Settings,
        *,
        api_key: str,
        user_name: str,
        base_url: str | None = None,
    ) -> dict[str, Any]:
        current = self.get_config(owner_id, settings, user_name=user_name)
        manual_api_key = str(current.get("manual_api_key") or "")
        previous_managed_api_key = str(current.get("managed_api_key") or "")
        payload = {
            "managed_api_key": api_key,
            "base_url": (base_url or settings.provider_base_url).rstrip("/"),
            "usage_path": settings.provider_usage_path,
            "model": current.get("model") or settings.image_model,
            "user_name": user_name,
            "managed_by_auth": 1,
        }
        preserve_manual_override = bool(current.get("managed_by_auth")) and bool(
            manual_api_key and manual_api_key != previous_managed_api_key
        )
        if not preserve_manual_override:
            payload["api_key"] = ""
        return self.update_config(owner_id, settings, payload)

    def merge_owner_data(
        self,
        from_owner_id: str,
        to_owner_id: str,
        settings: Settings,
        user_name: str | None = None,
    ) -> None:
        if from_owner_id == to_owner_id:
            return

        with self.connect() as conn:
            source_config = conn.execute(
                "SELECT * FROM owner_config WHERE owner_id = ?",
                (from_owner_id,),
            ).fetchone()
            target_config = conn.execute(
                "SELECT * FROM owner_config WHERE owner_id = ?",
                (to_owner_id,),
            ).fetchone()

            if source_config is not None and target_config is None:
                self._insert_owner_config(
                    conn,
                    to_owner_id,
                    settings,
                    {
                        "base_url": source_config["base_url"],
                        "usage_path": source_config["usage_path"],
                        "model": source_config["model"],
                        "default_size": source_config["default_size"],
                        "default_quality": source_config["default_quality"],
                        "user_name": user_name or source_config["user_name"],
                        "managed_by_auth": 0,
                    },
                )
            elif source_config is not None and target_config is not None:
                conn.execute(
                    """
                    UPDATE owner_config
                    SET default_size = COALESCE(NULLIF(default_size, ''), ?),
                        default_quality = COALESCE(NULLIF(default_quality, ''), ?),
                        updated_at = ?
                    WHERE owner_id = ?
                    """,
                    (
                        source_config["default_size"],
                        source_config["default_quality"],
                        utc_now(),
                        to_owner_id,
                    ),
                )

            conn.execute("UPDATE image_history SET owner_id = ? WHERE owner_id = ?", (to_owner_id, from_owner_id))
            conn.execute("UPDATE image_tasks SET owner_id = ? WHERE owner_id = ?", (to_owner_id, from_owner_id))
            conn.execute("UPDATE ledger_entries SET owner_id = ? WHERE owner_id = ?", (to_owner_id, from_owner_id))
            conn.execute("UPDATE OR IGNORE inspiration_favorites SET owner_id = ? WHERE owner_id = ?", (to_owner_id, from_owner_id))
            conn.execute("UPDATE OR IGNORE trial_grants SET owner_id = ?, updated_at = ? WHERE owner_id = ?", (to_owner_id, utc_now(), from_owner_id))
            conn.execute("DELETE FROM inspiration_favorites WHERE owner_id = ?", (from_owner_id,))
            conn.execute("DELETE FROM trial_grants WHERE owner_id = ?", (from_owner_id,))
            conn.execute("DELETE FROM owner_config WHERE owner_id = ?", (from_owner_id,))

    def create_history(self, owner_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        record = {
            "id": payload.get("id") or uuid4().hex,
            "owner_id": owner_id,
            "task_id": payload.get("task_id"),
            "batch_index": int(payload.get("batch_index") or 0),
            "mode": payload["mode"],
            "prompt": payload["prompt"],
            "model": payload["model"],
            "size": payload["size"],
            "aspect_ratio": payload.get("aspect_ratio") or "",
            "quality": payload["quality"],
            "status": payload["status"],
            "image_url": payload.get("image_url"),
            "image_path": payload.get("image_path"),
            "input_image_url": payload.get("input_image_url"),
            "input_image_path": payload.get("input_image_path"),
            "revised_prompt": payload.get("revised_prompt"),
            "usage_json": _json_or_none(payload.get("usage")),
            "provider_response_json": _json_or_none(payload.get("provider_response")),
            "error": payload.get("error"),
            "created_at": now,
            "updated_at": now,
        }
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO image_history (
                    id, owner_id, task_id, batch_index, mode, prompt, model, size, aspect_ratio, quality, status, image_url, image_path,
                    input_image_url, input_image_path, revised_prompt, usage_json,
                    provider_response_json, error, created_at, updated_at
                )
                VALUES (
                    :id, :owner_id, :task_id, :batch_index, :mode, :prompt, :model, :size, :aspect_ratio, :quality, :status, :image_url,
                    :image_path, :input_image_url, :input_image_path, :revised_prompt,
                    :usage_json, :provider_response_json, :error, :created_at, :updated_at
                )
                """,
                record,
            )
        return self.get_history(owner_id, record["id"])

    def update_history(self, owner_id: str, history_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        allowed = {
            "task_id",
            "batch_index",
            "mode",
            "prompt",
            "model",
            "size",
            "aspect_ratio",
            "quality",
            "status",
            "image_url",
            "image_path",
            "input_image_url",
            "input_image_path",
            "revised_prompt",
            "usage",
            "provider_response",
            "error",
        }
        updates: dict[str, Any] = {}
        for key, value in payload.items():
            if key not in allowed:
                continue
            if key == "usage":
                updates["usage_json"] = _json_or_none(value)
            elif key == "provider_response":
                updates["provider_response_json"] = _json_or_none(value)
            else:
                updates[key] = value
        if not updates:
            return self.get_history(owner_id, history_id)
        updates["updated_at"] = utc_now()
        assignments = ", ".join(f"{key} = ?" for key in updates)
        with self.connect() as conn:
            result = conn.execute(
                f"UPDATE image_history SET {assignments} WHERE owner_id = ? AND id = ?",
                (*updates.values(), owner_id, history_id),
            )
            if result.rowcount == 0:
                return None
        return self.get_history(owner_id, history_id)

    def get_history(self, owner_id: str, history_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT h.*, p.id AS published_inspiration_id, p.created_at AS published_at,
                       t.prompt AS task_prompt, t.result_json AS task_result_json, t.request_json AS task_request_json
                FROM image_history h
                LEFT JOIN inspiration_prompts p
                    ON p.source_url = ? AND p.source_item_id = h.id
                LEFT JOIN image_tasks t
                    ON t.id = h.task_id AND t.owner_id = h.owner_id
                WHERE h.owner_id = ? AND h.id = ?
                """,
                (USER_GALLERY_SOURCE_URL, owner_id, history_id),
            ).fetchone()
        return _history_row(row) if row else None

    def list_history(self, owner_id: str, limit: int = 30, offset: int = 0, q: str = "") -> list[dict[str, Any]]:
        limit = max(1, min(limit, 100))
        offset = max(0, offset)
        search = f"%{q.strip().lower()}%"
        with self.connect() as conn:
            if q.strip():
                rows = conn.execute(
                    """
                    SELECT h.*, p.id AS published_inspiration_id, p.created_at AS published_at,
                           t.prompt AS task_prompt, t.result_json AS task_result_json, t.request_json AS task_request_json
                    FROM image_history h
                    LEFT JOIN inspiration_prompts p
                        ON p.source_url = ? AND p.source_item_id = h.id
                    LEFT JOIN image_tasks t
                        ON t.id = h.task_id AND t.owner_id = h.owner_id
                    WHERE h.owner_id = ? AND lower(h.prompt) LIKE ?
                    ORDER BY h.created_at DESC, h.batch_index ASC, h.id DESC
                    LIMIT ? OFFSET ?
                    """,
                    (USER_GALLERY_SOURCE_URL, owner_id, search, limit, offset),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT h.*, p.id AS published_inspiration_id, p.created_at AS published_at,
                           t.prompt AS task_prompt, t.result_json AS task_result_json, t.request_json AS task_request_json
                    FROM image_history h
                    LEFT JOIN inspiration_prompts p
                        ON p.source_url = ? AND p.source_item_id = h.id
                    LEFT JOIN image_tasks t
                        ON t.id = h.task_id AND t.owner_id = h.owner_id
                    WHERE h.owner_id = ?
                    ORDER BY h.created_at DESC, h.batch_index ASC, h.id DESC
                    LIMIT ? OFFSET ?
                    """,
                    (USER_GALLERY_SOURCE_URL, owner_id, limit, offset),
                ).fetchall()
        return [_history_row(row) for row in rows]

    def delete_history(self, owner_id: str, history_id: str) -> bool:
        with self.connect() as conn:
            conn.execute(
                """
                DELETE FROM inspiration_prompts
                WHERE source_url = ?
                  AND source_item_id = ?
                  AND EXISTS (
                    SELECT 1 FROM image_history
                    WHERE owner_id = ? AND id = ?
                  )
                """,
                (USER_GALLERY_SOURCE_URL, history_id, owner_id, history_id),
            )
            result = conn.execute(
                "DELETE FROM image_history WHERE owner_id = ? AND id = ?",
                (owner_id, history_id),
            )
            return result.rowcount > 0

    def create_image_task(self, owner_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        record = {
            "id": payload.get("id") or uuid4().hex,
            "owner_id": owner_id,
            "mode": payload["mode"],
            "prompt": payload["prompt"],
            "model": payload["model"],
            "size": payload["size"],
            "aspect_ratio": payload.get("aspect_ratio") or "",
            "quality": payload["quality"],
            "status": payload.get("status", "queued"),
            "request_json": _json_or_none(payload.get("request")),
            "input_image_url": payload.get("input_image_url"),
            "input_image_path": payload.get("input_image_path"),
            "result_history_ids_json": _json_or_none(payload.get("result_history_ids") or []),
            "result_json": _json_or_none(payload.get("result")),
            "error": payload.get("error"),
            "created_at": now,
            "updated_at": now,
            "started_at": payload.get("started_at"),
            "completed_at": payload.get("completed_at"),
        }
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO image_tasks (
                    id, owner_id, mode, prompt, model, size, aspect_ratio, quality, status, request_json,
                    input_image_url, input_image_path, result_history_ids_json, result_json, error,
                    created_at, updated_at, started_at, completed_at
                )
                VALUES (
                    :id, :owner_id, :mode, :prompt, :model, :size, :aspect_ratio, :quality, :status, :request_json,
                    :input_image_url, :input_image_path, :result_history_ids_json, :result_json, :error,
                    :created_at, :updated_at, :started_at, :completed_at
                )
                """,
                record,
            )
        return self.get_image_task(owner_id, record["id"])

    def get_image_task(self, owner_id: str, task_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM image_tasks WHERE owner_id = ? AND id = ?",
                (owner_id, task_id),
            ).fetchone()
        if row is None:
            return None
        return _image_task_row(row)

    def list_image_tasks(
        self,
        owner_id: str,
        limit: int = 20,
        statuses: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 100))
        clauses = ["owner_id = ?"]
        params: list[Any] = [owner_id]
        if statuses:
            placeholders = ", ".join("?" for _ in statuses)
            clauses.append(f"status IN ({placeholders})")
            params.extend(statuses)
        where = " AND ".join(clauses)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM image_tasks
                WHERE {where}
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (*params, limit),
            ).fetchall()
        return [_image_task_row(row) for row in rows]

    def update_image_task(self, task_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        updates: dict[str, Any] = {}
        for key, value in payload.items():
            if key in {"status", "input_image_url", "input_image_path", "error", "started_at", "completed_at"}:
                updates[key] = value
            elif key == "request":
                updates["request_json"] = _json_or_none(value)
            elif key == "request_json":
                updates["request_json"] = value
            elif key == "result_history_ids":
                updates["result_history_ids_json"] = _json_or_none(value or [])
            elif key == "result_history_ids_json":
                updates["result_history_ids_json"] = value
            elif key == "result":
                updates["result_json"] = _json_or_none(value)
            elif key == "result_json":
                updates["result_json"] = value
        if not updates:
            return self.get_image_task_by_id(task_id)
        with self.connect() as conn:
            row = conn.execute("SELECT owner_id FROM image_tasks WHERE id = ?", (task_id,)).fetchone()
            if row is None:
                return None
            updates["updated_at"] = utc_now()
            assignments = ", ".join(f"{key} = ?" for key in updates)
            values = list(updates.values())
            values.append(task_id)
            conn.execute(f"UPDATE image_tasks SET {assignments} WHERE id = ?", values)
        return self.get_image_task(str(row["owner_id"]), task_id)

    def get_image_task_by_id(self, task_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM image_tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            return None
        return _image_task_row(row)

    def get_history_items(self, owner_id: str, history_ids: list[str]) -> list[dict[str, Any]]:
        if not history_ids:
            return []
        placeholders = ", ".join("?" for _ in history_ids)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT h.*, p.id AS published_inspiration_id, p.created_at AS published_at,
                       t.prompt AS task_prompt, t.result_json AS task_result_json, t.request_json AS task_request_json
                FROM image_history h
                LEFT JOIN inspiration_prompts p
                    ON p.source_url = ? AND p.source_item_id = h.id
                LEFT JOIN image_tasks t
                    ON t.id = h.task_id AND t.owner_id = h.owner_id
                WHERE h.owner_id = ? AND h.id IN ({placeholders})
                """,
                (USER_GALLERY_SOURCE_URL, owner_id, *history_ids),
            ).fetchall()
        items: dict[str, dict[str, Any]] = {}
        for row in rows:
            record = _history_row(row)
            items[record["id"]] = record
        return [items[item_id] for item_id in history_ids if item_id in items]

    def publish_history_as_inspiration(self, owner_id: str, history_id: str, author: str) -> dict[str, Any] | None:
        now = utc_now()
        with self.connect() as conn:
            history = conn.execute(
                "SELECT * FROM image_history WHERE owner_id = ? AND id = ?",
                (owner_id, history_id),
            ).fetchone()
            if history is None:
                return None
            if history["status"] != "succeeded" or not history["image_url"]:
                raise ValueError("Only successful history items with an image can be published")

            record = {
                "id": f"user-{history_id}",
                "source_url": USER_GALLERY_SOURCE_URL,
                "source_item_id": history_id,
                "section": USER_GALLERY_SECTION,
                "title": _inspiration_title_from_prompt(history["prompt"]),
                "author": author,
                "prompt": history["prompt"],
                "image_url": history["image_url"],
                "source_link": None,
                "raw_json": _json_or_none(
                    {
                        "history_id": history_id,
                        "owner_id": owner_id,
                        "mode": history["mode"],
                        "model": history["model"],
                        "size": history["size"],
                        "aspect_ratio": history["aspect_ratio"],
                        "quality": history["quality"],
                        "history_created_at": history["created_at"],
                    }
                ),
                "synced_at": now,
                "created_at": now,
                "updated_at": now,
            }
            conn.execute(
                """
                INSERT INTO inspiration_prompts (
                    id, source_url, source_item_id, section, title, author, prompt,
                    image_url, source_link, raw_json, synced_at, created_at, updated_at
                )
                VALUES (
                    :id, :source_url, :source_item_id, :section, :title, :author,
                    :prompt, :image_url, :source_link, :raw_json, :synced_at,
                    :created_at, :updated_at
                )
                ON CONFLICT(source_url, source_item_id) DO UPDATE SET
                    section = excluded.section,
                    title = excluded.title,
                    author = excluded.author,
                    prompt = excluded.prompt,
                    image_url = excluded.image_url,
                    source_link = excluded.source_link,
                    raw_json = excluded.raw_json,
                    synced_at = excluded.synced_at,
                    updated_at = excluded.updated_at
                """,
                record,
            )
            row = conn.execute(
                "SELECT * FROM inspiration_prompts WHERE source_url = ? AND source_item_id = ?",
                (USER_GALLERY_SOURCE_URL, history_id),
            ).fetchone()
        return _inspiration_row(row) if row else None

    def unpublish_history_inspiration(self, owner_id: str, history_id: str) -> bool:
        with self.connect() as conn:
            result = conn.execute(
                """
                DELETE FROM inspiration_prompts
                WHERE source_url = ?
                  AND source_item_id = ?
                  AND EXISTS (
                    SELECT 1 FROM image_history
                    WHERE owner_id = ? AND id = ?
                  )
                """,
                (USER_GALLERY_SOURCE_URL, history_id, owner_id, history_id),
            )
            return result.rowcount > 0

    def fail_incomplete_tasks(self, message: str) -> int:
        now = utc_now()
        with self.connect() as conn:
            result = conn.execute(
                """
                UPDATE image_tasks
                SET status = 'failed',
                    error = ?,
                    completed_at = COALESCE(completed_at, ?),
                    updated_at = ?
                WHERE status IN ('queued', 'running')
                """,
                (message, now, now),
            )
            return int(result.rowcount or 0)

    def add_ledger_entry(self, owner_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        record = {
            "id": payload.get("id") or uuid4().hex,
            "owner_id": owner_id,
            "event_type": payload["event_type"],
            "amount": payload.get("amount", 0),
            "currency": payload.get("currency", "USD"),
            "description": payload["description"],
            "history_id": payload.get("history_id"),
            "metadata_json": _json_or_none(payload.get("metadata")),
            "created_at": utc_now(),
        }
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO ledger_entries (
                    id, owner_id, event_type, amount, currency, description, history_id,
                    metadata_json, created_at
                )
                VALUES (
                    :id, :owner_id, :event_type, :amount, :currency, :description, :history_id,
                    :metadata_json, :created_at
                )
                """,
                record,
            )
        return record

    def list_ledger(self, owner_id: str, limit: int = 20) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 100))
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM ledger_entries WHERE owner_id = ? ORDER BY created_at DESC LIMIT ?",
                (owner_id, limit),
            ).fetchall()
        return [_ledger_row(row) for row in rows]

    def create_session(
        self,
        *,
        owner_id: str,
        sub2api_user_id: int,
        email: str,
        username: str,
        role: str,
        ttl_seconds: int,
        access_token: str = "",
        refresh_token: str = "",
        user_agent: str | None = None,
        ip_address: str | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        record = {
            "id": secrets.token_urlsafe(32),
            "owner_id": owner_id,
            "sub2api_user_id": sub2api_user_id,
            "email": email,
            "username": username or "",
            "role": role or "user",
            "access_token": access_token,
            "refresh_token": refresh_token,
            "created_at": now,
            "updated_at": now,
            "expires_at": utc_after(ttl_seconds),
            "user_agent": user_agent,
            "ip_address": ip_address,
        }
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO user_sessions (
                    id, owner_id, sub2api_user_id, email, username, role, access_token, refresh_token,
                    created_at, updated_at, expires_at, user_agent, ip_address
                )
                VALUES (
                    :id, :owner_id, :sub2api_user_id, :email, :username, :role, :access_token, :refresh_token,
                    :created_at, :updated_at, :expires_at, :user_agent, :ip_address
                )
                """,
                record,
            )
        return record

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        if not session_id:
            return None
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM user_sessions WHERE id = ?", (session_id,)).fetchone()
            if row is None:
                return None
            data = dict(row)
            if _is_expired(data["expires_at"]):
                conn.execute("DELETE FROM user_sessions WHERE id = ?", (session_id,))
                return None
            return data

    def latest_session_for_owner(self, owner_id: str) -> dict[str, Any] | None:
        if not owner_id:
            return None
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM user_sessions
                WHERE owner_id = ?
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (owner_id,),
            ).fetchone()
            if row is None:
                return None
            data = dict(row)
            if _is_expired(data["expires_at"]):
                conn.execute("DELETE FROM user_sessions WHERE id = ?", (data["id"],))
                return None
            return data

    def touch_session(self, session_id: str, ttl_seconds: int) -> None:
        if not session_id:
            return
        with self.connect() as conn:
            conn.execute(
                "UPDATE user_sessions SET updated_at = ?, expires_at = ? WHERE id = ?",
                (utc_now(), utc_after(ttl_seconds), session_id),
            )

    def delete_session(self, session_id: str) -> None:
        if not session_id:
            return
        with self.connect() as conn:
            conn.execute("DELETE FROM user_sessions WHERE id = ?", (session_id,))

    def get_trial_grant(self, owner_id: str | None = None, sub2api_user_id: int | None = None) -> dict[str, Any] | None:
        if not owner_id and sub2api_user_id is None:
            return None
        clause = "owner_id = ?"
        value: Any = owner_id
        if sub2api_user_id is not None:
            clause = "sub2api_user_id = ?"
            value = sub2api_user_id
        with self.connect() as conn:
            row = conn.execute(f"SELECT * FROM trial_grants WHERE {clause}", (value,)).fetchone()
        return dict(row) if row else None

    def mark_trial_grant(
        self,
        *,
        owner_id: str,
        sub2api_user_id: int,
        email: str,
        key_id: str | None = None,
        key_hint: str = "",
        quota_usd: float = 0,
        balance_granted_usd: float = 0,
        status: str = "created",
        error: str | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        record = {
            "owner_id": owner_id,
            "sub2api_user_id": sub2api_user_id,
            "email": email,
            "key_id": key_id,
            "key_hint": key_hint,
            "quota_usd": quota_usd,
            "balance_granted_usd": balance_granted_usd,
            "status": status,
            "error": error,
            "created_at": now,
            "updated_at": now,
        }
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO trial_grants (
                    owner_id, sub2api_user_id, email, key_id, key_hint, quota_usd,
                    balance_granted_usd, status, error, created_at, updated_at
                )
                VALUES (
                    :owner_id, :sub2api_user_id, :email, :key_id, :key_hint, :quota_usd,
                    :balance_granted_usd, :status, :error, :created_at, :updated_at
                )
                ON CONFLICT(owner_id) DO UPDATE SET
                    sub2api_user_id = excluded.sub2api_user_id,
                    email = excluded.email,
                    key_id = COALESCE(excluded.key_id, trial_grants.key_id),
                    key_hint = COALESCE(NULLIF(excluded.key_hint, ''), trial_grants.key_hint),
                    quota_usd = CASE WHEN excluded.quota_usd > 0 THEN excluded.quota_usd ELSE trial_grants.quota_usd END,
                    balance_granted_usd = CASE
                        WHEN excluded.balance_granted_usd > 0 THEN excluded.balance_granted_usd
                        ELSE trial_grants.balance_granted_usd
                    END,
                    status = excluded.status,
                    error = excluded.error,
                    updated_at = excluded.updated_at
                """,
                record,
            )
            row = conn.execute("SELECT * FROM trial_grants WHERE owner_id = ?", (owner_id,)).fetchone()
        return dict(row) if row else record

    def stats(self, owner_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END) AS succeeded,
                    SUM(CASE WHEN mode = 'edit' THEN 1 ELSE 0 END) AS edits,
                    MAX(created_at) AS last_generation_at
                FROM image_history
                WHERE owner_id = ?
                """,
                (owner_id,),
            ).fetchone()
        return {
            "total": int(row["total"] or 0),
            "succeeded": int(row["succeeded"] or 0),
            "edits": int(row["edits"] or 0),
            "last_generation_at": row["last_generation_at"],
        }

    def upsert_inspirations(self, source_url: str, items: list[dict[str, Any]]) -> dict[str, Any]:
        now = utc_now()
        changed = 0
        with self.connect() as conn:
            for item in items:
                record = {
                    "id": item["id"],
                    "source_url": source_url,
                    "source_item_id": item["source_item_id"],
                    "section": item["section"],
                    "title": item["title"],
                    "author": item.get("author"),
                    "prompt": item["prompt"],
                    "image_url": item.get("image_url"),
                    "source_link": item.get("source_link"),
                    "raw_json": _json_or_none(item.get("raw")),
                    "synced_at": now,
                    "created_at": now,
                    "updated_at": now,
                }
                conn.execute(
                    """
                    INSERT INTO inspiration_prompts (
                        id, source_url, source_item_id, section, title, author, prompt,
                        image_url, source_link, raw_json, synced_at, created_at, updated_at
                    )
                    VALUES (
                        :id, :source_url, :source_item_id, :section, :title, :author,
                        :prompt, :image_url, :source_link, :raw_json, :synced_at,
                        :created_at, :updated_at
                    )
                    ON CONFLICT(source_url, source_item_id) DO UPDATE SET
                        section = excluded.section,
                        title = excluded.title,
                        author = excluded.author,
                        prompt = excluded.prompt,
                        image_url = excluded.image_url,
                        source_link = excluded.source_link,
                        raw_json = excluded.raw_json,
                        synced_at = excluded.synced_at,
                        updated_at = excluded.updated_at
                    """,
                    record,
                )
                changed += 1
        return {"count": changed, "synced_at": now}

    def list_inspirations(
        self,
        limit: int = 48,
        offset: int = 0,
        q: str = "",
        section: str = "",
        favorite_owner_id: str | None = None,
        favorites_only: bool = False,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 200))
        offset = max(0, offset)
        where, params = self._inspiration_where(q=q, section=section, table_alias="p")
        favorite_select = "0 AS favorited, NULL AS favorite_created_at"
        favorite_join = ""
        favorite_params: list[Any] = []
        order_by = "p.created_at DESC, p.synced_at DESC, p.section ASC, p.title ASC"
        if favorite_owner_id:
            favorite_params.append(favorite_owner_id)
            favorite_select = "CASE WHEN f.owner_id IS NULL THEN 0 ELSE 1 END AS favorited, f.created_at AS favorite_created_at"
            favorite_join = "LEFT JOIN inspiration_favorites f ON f.inspiration_id = p.id AND f.owner_id = ?"
            if favorites_only:
                favorite_select = "1 AS favorited, f.created_at AS favorite_created_at"
                favorite_join = "JOIN inspiration_favorites f ON f.inspiration_id = p.id AND f.owner_id = ?"
                order_by = "f.created_at DESC, p.created_at DESC, p.synced_at DESC, p.section ASC, p.title ASC"
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT p.*, {favorite_select}
                FROM inspiration_prompts p
                {favorite_join}
                {where}
                ORDER BY {order_by}
                LIMIT ? OFFSET ?
                """,
                (*favorite_params, *params, limit, offset),
            ).fetchall()
        return [_inspiration_row(row) for row in rows]

    def count_inspirations(
        self,
        q: str = "",
        section: str = "",
        favorite_owner_id: str | None = None,
        favorites_only: bool = False,
    ) -> int:
        where, params = self._inspiration_where(q=q, section=section, table_alias="p")
        favorite_join = ""
        favorite_params: list[Any] = []
        if favorite_owner_id and favorites_only:
            favorite_join = "JOIN inspiration_favorites f ON f.inspiration_id = p.id AND f.owner_id = ?"
            favorite_params.append(favorite_owner_id)
        with self.connect() as conn:
            row = conn.execute(
                f"""
                SELECT COUNT(*) AS total
                FROM inspiration_prompts p
                {favorite_join}
                {where}
                """,
                (*favorite_params, *params),
            ).fetchone()
        return int(row["total"] if row else 0)

    def get_inspiration(self, inspiration_id: str, favorite_owner_id: str | None = None) -> dict[str, Any] | None:
        favorite_select = "0 AS favorited, NULL AS favorite_created_at"
        favorite_join = ""
        params: list[Any] = []
        if favorite_owner_id:
            favorite_select = "CASE WHEN f.owner_id IS NULL THEN 0 ELSE 1 END AS favorited, f.created_at AS favorite_created_at"
            favorite_join = "LEFT JOIN inspiration_favorites f ON f.inspiration_id = p.id AND f.owner_id = ?"
            params.append(favorite_owner_id)
        params.append(inspiration_id)
        with self.connect() as conn:
            row = conn.execute(
                f"""
                SELECT p.*, {favorite_select}
                FROM inspiration_prompts p
                {favorite_join}
                WHERE p.id = ?
                """,
                params,
            ).fetchone()
        return _inspiration_row(row) if row else None

    def set_inspiration_favorite(self, owner_id: str, inspiration_id: str, favorited: bool) -> dict[str, Any] | None:
        with self.connect() as conn:
            exists = conn.execute("SELECT id FROM inspiration_prompts WHERE id = ?", (inspiration_id,)).fetchone()
            if exists is None:
                return None
            if favorited:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO inspiration_favorites (owner_id, inspiration_id, created_at)
                    VALUES (?, ?, ?)
                    """,
                    (owner_id, inspiration_id, utc_now()),
                )
            else:
                conn.execute(
                    "DELETE FROM inspiration_favorites WHERE owner_id = ? AND inspiration_id = ?",
                    (owner_id, inspiration_id),
                )
        return self.get_inspiration(inspiration_id, favorite_owner_id=owner_id)

    @staticmethod
    def _inspiration_where(q: str = "", section: str = "", table_alias: str = "") -> tuple[str, list[Any]]:
        prefix = f"{table_alias}." if table_alias else ""
        clauses = []
        params: list[Any] = []
        if q.strip():
            clauses.append(f"(lower({prefix}title) LIKE ? OR lower({prefix}prompt) LIKE ? OR lower({prefix}author) LIKE ?)")
            search = f"%{q.strip().lower()}%"
            params.extend([search, search, search])
        if section.strip():
            clauses.append(f"{prefix}section = ?")
            params.append(section.strip())
        return (f"WHERE {' AND '.join(clauses)}" if clauses else "", params)

    def inspiration_stats(self) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    MAX(synced_at) AS last_synced_at,
                    COUNT(DISTINCT section) AS sections
                FROM inspiration_prompts
                """
            ).fetchone()
            section_rows = conn.execute(
                """
                SELECT section, COUNT(*) AS count
                FROM inspiration_prompts
                GROUP BY section
                ORDER BY section ASC
                """
            ).fetchall()
            source_rows = conn.execute(
                """
                SELECT source_url, COUNT(*) AS count, MAX(synced_at) AS last_synced_at
                FROM inspiration_prompts
                GROUP BY source_url
                ORDER BY last_synced_at DESC, source_url ASC
                """
            ).fetchall()
        return {
            "total": int(row["total"] or 0),
            "last_synced_at": row["last_synced_at"],
            "sections": int(row["sections"] or 0),
            "section_counts": [{"section": item["section"], "count": int(item["count"])} for item in section_rows],
            "source_counts": [
                {
                    "source_url": item["source_url"],
                    "count": int(item["count"] or 0),
                    "last_synced_at": item["last_synced_at"],
                }
                for item in source_rows
            ],
        }


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(conn, table):
        return set()
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {str(row["name"]) for row in rows}


def _is_expired(value: str | None) -> bool:
    if not value:
        return True
    return datetime.fromisoformat(value) <= datetime.now(timezone.utc)


def _json_or_none(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def _json_load(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def _history_row(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["usage"] = _json_load(data.pop("usage_json"))
    data["provider_response"] = _json_load(data.pop("provider_response_json"))
    task_result = _json_load(data.pop("task_result_json", None))
    task_request = _json_load(data.pop("task_request_json", None))
    data["task_prompt"] = data.get("task_prompt")
    data["task_result"] = task_result
    data["task_request"] = _public_task_request_metadata(task_request)
    data["published_inspiration_id"] = data.get("published_inspiration_id")
    data["published_at"] = data.get("published_at")
    data["published"] = bool(data["published_inspiration_id"])
    return data


def _public_task_request_metadata(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    metadata: dict[str, Any] = {}
    reference_notes = value.get("reference_notes")
    if isinstance(reference_notes, list):
        metadata["reference_notes"] = [
            {
                "index": item.get("index"),
                "role": item.get("role") or "",
                "note": item.get("note") or "",
                "url": item.get("url") or "",
                "primary": bool(item.get("primary")),
                "explicit": bool(item.get("explicit")),
            }
            for item in reference_notes
            if isinstance(item, dict)
        ]
    ecommerce = value.get("ecommerce")
    if isinstance(ecommerce, dict):
        metadata["ecommerce"] = {
            "product_name": ecommerce.get("product_name") or "",
            "materials": ecommerce.get("materials") or "",
            "selling_points": ecommerce.get("selling_points") or "",
            "scenarios": ecommerce.get("scenarios") or "",
            "platform": ecommerce.get("platform") or "",
            "style": ecommerce.get("style") or "",
            "extra_requirements": ecommerce.get("extra_requirements") or "",
            "analysis": ecommerce.get("analysis") if isinstance(ecommerce.get("analysis"), dict) else None,
        }
    return metadata or None


def _ledger_row(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["metadata"] = _json_load(data.pop("metadata_json"))
    return data


def _image_task_row(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["request"] = _json_load(data.pop("request_json"))
    data["result_history_ids"] = _json_load(data.pop("result_history_ids_json")) or []
    data["result"] = _json_load(data.pop("result_json"))
    return data


def _inspiration_row(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["raw"] = _json_load(data.pop("raw_json"))
    data["favorited"] = bool(data.get("favorited", 0))
    data["favorite_created_at"] = data.get("favorite_created_at")
    return data


def _inspiration_title_from_prompt(prompt: str) -> str:
    compact = " ".join(prompt.split())
    if len(compact) <= 48:
        return compact or "用户作品"
    return f"{compact[:48].rstrip()}..."


def _site_settings_row(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    data = dict(row)
    data["inspiration_sources"] = _json_load(data.pop("inspiration_sources_json", None)) or []
    if "trial_balance_usd" not in data:
        data["trial_balance_usd"] = None
    return data


def _config_row(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    data = dict(row)
    manual_api_key = str(data.get("api_key") or "")
    managed_api_key = str(data.get("managed_api_key") or "")
    effective_api_key = manual_api_key or managed_api_key
    data["manual_api_key"] = manual_api_key
    data["managed_api_key"] = managed_api_key
    data["api_key_source"] = _config_api_key_source(data)
    data["api_key"] = effective_api_key
    return data


def _config_api_key_source(config: dict[str, Any]) -> str:
    if config.get("managed_by_auth"):
        manual_api_key = str(config.get("api_key") or "")
        managed_api_key = str(config.get("managed_api_key") or "")
        if manual_api_key and manual_api_key != managed_api_key:
            return "manual_override"
        if managed_api_key:
            return "managed"
    return "manual"
