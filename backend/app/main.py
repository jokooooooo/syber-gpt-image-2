from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlparse
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .auth_client import Sub2APIAuthClient
from .db import Database, utc_now
from .inspirations import normalize_inspiration_source_urls, run_inspiration_sync_loop, sync_inspirations
from .provider import OpenAICompatibleImageClient, ProviderError
from .settings import Settings
from .storage import save_provider_image, save_upload


class ConfigUpdate(BaseModel):
    api_key: str | None = None
    clear_api_key: bool = False
    base_url: str | None = None
    usage_path: str | None = None
    model: str | None = None
    default_size: str | None = None
    default_quality: str | None = None
    user_name: str | None = None


class GenerateRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=8000)
    model: str | None = None
    size: str | None = None
    aspect_ratio: str | None = None
    quality: str | None = None
    n: int = Field(default=1, ge=1, le=4)
    background: str | None = None
    output_format: str | None = None


class PromptOptimizeRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=12000)
    instruction: str | None = Field(default=None, max_length=2000)
    model: str | None = Field(default=None, max_length=120)
    size: str | None = Field(default=None, max_length=80)
    aspect_ratio: str | None = Field(default=None, max_length=20)
    quality: str | None = Field(default=None, max_length=40)


SIZE_PRESETS: dict[str, dict[str, str]] = {
    "1K": {
        "1:1": "1088x1088",
        "16:9": "2048x1152",
        "9:16": "1152x2048",
        "3:2": "1632x1088",
        "2:3": "1088x1632",
        "4:3": "1472x1104",
        "3:4": "1104x1472",
    },
    "2K": {
        "1:1": "1440x1440",
        "16:9": "2560x1440",
        "9:16": "1440x2560",
        "3:2": "2160x1440",
        "2:3": "1440x2160",
        "4:3": "1920x1440",
        "3:4": "1440x1920",
    },
    "4K": {
        "16:9": "3840x2160",
        "9:16": "2160x3840",
        "3:2": "3840x2560",
        "2:3": "2560x3840",
        "4:3": "3840x2880",
        "3:4": "2880x3840",
    },
}

SIZE_TIER_BY_DIMENSION = {
    dimension.lower(): scale for scale, ratios in SIZE_PRESETS.items() for dimension in ratios.values()
}

RETRYABLE_PROVIDER_STATUS_CODES = {429, 502, 503, 504}
IMAGE_PROVIDER_MAX_ATTEMPTS = 3
PROMPT_OPTIMIZER_SYSTEM_PROMPT = """你是 JokoAI 的图像生成提示词优化器。
用户会提供一段原始生图提示词，以及可选的修改要求。你的任务是输出一段可以直接用于 gpt-image-2 / OpenAI 兼容生图接口的最终提示词。
要求：
1. 只输出最终提示词，不要标题、解释、Markdown、代码块或引号。
2. 尽量保留原提示词的构图、风格、镜头、场景和关键约束。
3. 如果用户给出角色、主体、商品、场景或风格替换要求，优先按修改要求替换旧内容。
4. 补强画面主体、环境、光线、材质、镜头、细节、商业可用性和高质量图像描述。
5. 避免加入水印、乱码文字、错误品牌标识、低清、畸形手指、额外肢体等负面结果。
6. 保持原提示词主要语言；中文输入输出中文，英文输入输出英文。
7. 输出要具体但不要冗长，适合直接复制到生图框。"""


class AuthSendVerifyCodeRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    turnstile_token: str | None = None


class AuthRegisterRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=6, max_length=256)
    verify_code: str | None = None
    turnstile_token: str | None = None
    promo_code: str | None = None
    invitation_code: str | None = None


class AuthLoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=256)
    turnstile_token: str | None = None


class AuthLogin2FARequest(BaseModel):
    temp_token: str = Field(min_length=1, max_length=2048)
    totp_code: str = Field(min_length=6, max_length=6)


class SiteSettingsUpdate(BaseModel):
    default_locale: str | None = None
    announcement_enabled: bool | None = None
    announcement_title: str | None = Field(default=None, max_length=120)
    announcement_body: str | None = Field(default=None, max_length=12000)
    inspiration_sources: list[str] | None = None
    provider_base_url: str | None = None
    auth_base_url: str | None = None


@dataclass
class ViewerContext:
    owner_id: str
    guest_owner_id: str
    guest_id: str
    authenticated: bool
    session_id: str | None
    session: dict[str, Any] | None

    @property
    def user(self) -> dict[str, Any] | None:
        if not self.session:
            return None
        return {
            "id": self.session["sub2api_user_id"],
            "email": self.session["email"],
            "username": self.session["username"],
            "role": self.session["role"],
        }

    @property
    def is_admin(self) -> bool:
        user = self.user
        return bool(user and user.get("role") == "admin")


@dataclass(frozen=True)
class ImageLedgerCost:
    amount: float
    source: str
    usage_log: dict[str, Any] | None = None


def create_app(
    settings: Settings | None = None,
    provider: OpenAICompatibleImageClient | None = None,
    auth_client: Sub2APIAuthClient | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.ensure_directories()
    db = Database(settings.database_path)
    db.init(settings)
    db.fail_incomplete_tasks("Worker restarted before the task completed")
    _backfill_zero_amount_ledger(db, settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if settings.inspiration_sync_on_startup or settings.inspiration_sync_interval_seconds > 0:
            app.state.inspiration_task = asyncio.create_task(run_inspiration_sync_loop(app))
        try:
            yield
        finally:
            task = app.state.inspiration_task
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            pending_image_tasks = list(app.state.image_tasks.values())
            for image_task in pending_image_tasks:
                image_task.cancel()
            for image_task in pending_image_tasks:
                try:
                    await image_task
                except asyncio.CancelledError:
                    pass

    app = FastAPI(title="CyberGen Backend", version="2.0.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.db = db
    app.state.provider = provider or OpenAICompatibleImageClient(settings.request_timeout_seconds)
    app.state.auth_client = auth_client or Sub2APIAuthClient(settings.request_timeout_seconds)
    app.state.inspiration_task = None
    app.state.image_tasks = {}
    app.state.last_inspiration_sync = None
    app.state.last_inspiration_sync_error = None
    app.dependency_overrides[_db] = lambda: app.state.db
    app.dependency_overrides[_settings] = lambda: app.state.settings
    app.dependency_overrides[_provider] = lambda: app.state.provider
    app.dependency_overrides[_auth_client] = lambda: app.state.auth_client

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins or ["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.mount("/storage", StaticFiles(directory=settings.storage_dir), name="storage")

    @app.middleware("http")
    async def attach_viewer(request: Request, call_next):
        request.state.clear_session_cookie = False
        guest_id = request.cookies.get(settings.guest_cookie_name) or uuid4().hex
        request.state.guest_id = guest_id
        request.state.guest_owner_id = f"guest:{guest_id}"
        request.state.viewer_session = None
        request.state.viewer_owner_id = request.state.guest_owner_id

        session_id = request.cookies.get(settings.session_cookie_name)
        if session_id:
            session = db.get_session(session_id)
            if session is None:
                request.state.clear_session_cookie = True
            else:
                db.touch_session(session_id, settings.session_ttl_seconds)
                request.state.viewer_session = session
                request.state.viewer_owner_id = session["owner_id"]

        response = await call_next(request)
        _set_guest_cookie(response, settings, guest_id)
        if request.state.clear_session_cookie:
            response.delete_cookie(settings.session_cookie_name, path="/")
        return response

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {
            "ok": "true",
            "inspirations": db.inspiration_stats(),
            "last_inspiration_sync_error": app.state.last_inspiration_sync_error,
        }

    @app.get("/api/auth/public-settings")
    async def auth_public_settings(
        db: Database = Depends(_db),
        settings: Settings = Depends(_settings),
        auth_client: Sub2APIAuthClient = Depends(_auth_client),
    ) -> dict[str, Any]:
        try:
            return await auth_client.public_settings(_site_auth_base_url(db, settings))
        except ProviderError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    @app.get("/api/auth/session")
    async def auth_session(
        viewer: ViewerContext = Depends(_viewer),
        db: Database = Depends(_db),
        settings: Settings = Depends(_settings),
    ) -> dict[str, Any]:
        config = db.get_config(viewer.owner_id, settings, user_name=_viewer_name(viewer, settings))
        return _viewer_payload(viewer, config)

    @app.get("/api/site-settings")
    async def get_site_settings(
        viewer: ViewerContext = Depends(_viewer),
        db: Database = Depends(_db),
        settings: Settings = Depends(_settings),
    ) -> dict[str, Any]:
        return _public_site_settings(db.get_site_settings(), viewer, settings)

    @app.put("/api/site-settings")
    async def update_site_settings(
        payload: SiteSettingsUpdate,
        viewer: ViewerContext = Depends(_viewer),
        db: Database = Depends(_db),
        settings: Settings = Depends(_settings),
    ) -> dict[str, Any]:
        _require_admin(viewer)
        updates = payload.model_dump(exclude_none=True)
        if "inspiration_sources" in updates:
            sources = normalize_inspiration_source_urls(updates["inspiration_sources"])
            if not sources:
                raise HTTPException(status_code=400, detail="At least one case source is required")
            updates["inspiration_sources"] = sources
        for key in ("provider_base_url", "auth_base_url"):
            if key in updates:
                updates[key] = _normalize_upstream_url(updates[key])
        return _public_site_settings(db.update_site_settings(updates), viewer, settings)

    @app.post("/api/auth/send-verify-code")
    async def auth_send_verify_code(
        payload: AuthSendVerifyCodeRequest,
        db: Database = Depends(_db),
        settings: Settings = Depends(_settings),
        auth_client: Sub2APIAuthClient = Depends(_auth_client),
    ) -> dict[str, Any]:
        try:
            body = payload.model_dump(exclude_none=True)
            return await auth_client.send_verify_code(_site_auth_base_url(db, settings), body)
        except ProviderError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    @app.post("/api/auth/register")
    async def auth_register(
        payload: AuthRegisterRequest,
        request: Request,
        response: Response,
        db: Database = Depends(_db),
        settings: Settings = Depends(_settings),
        auth_client: Sub2APIAuthClient = Depends(_auth_client),
    ) -> dict[str, Any]:
        try:
            result = await auth_client.register(_site_auth_base_url(db, settings), payload.model_dump(exclude_none=True))
            viewer_payload = await _complete_auth_flow(
                db,
                settings,
                auth_client,
                request,
                response,
                result,
            )
            return {"ok": True, "viewer": viewer_payload}
        except ProviderError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    @app.post("/api/auth/login")
    async def auth_login(
        payload: AuthLoginRequest,
        request: Request,
        response: Response,
        db: Database = Depends(_db),
        settings: Settings = Depends(_settings),
        auth_client: Sub2APIAuthClient = Depends(_auth_client),
    ) -> dict[str, Any]:
        try:
            result = await auth_client.login(_site_auth_base_url(db, settings), payload.model_dump(exclude_none=True))
            if isinstance(result, dict) and result.get("requires_2fa"):
                return {
                    "ok": True,
                    "requires_2fa": True,
                    "temp_token": result.get("temp_token"),
                    "user_email_masked": result.get("user_email_masked"),
                }
            viewer_payload = await _complete_auth_flow(
                db,
                settings,
                auth_client,
                request,
                response,
                result,
            )
            return {"ok": True, "viewer": viewer_payload}
        except ProviderError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    @app.post("/api/auth/login/2fa")
    async def auth_login_2fa(
        payload: AuthLogin2FARequest,
        request: Request,
        response: Response,
        db: Database = Depends(_db),
        settings: Settings = Depends(_settings),
        auth_client: Sub2APIAuthClient = Depends(_auth_client),
    ) -> dict[str, Any]:
        try:
            result = await auth_client.login_2fa(_site_auth_base_url(db, settings), payload.model_dump(exclude_none=True))
            viewer_payload = await _complete_auth_flow(
                db,
                settings,
                auth_client,
                request,
                response,
                result,
            )
            return {"ok": True, "viewer": viewer_payload}
        except ProviderError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    @app.post("/api/auth/logout")
    async def auth_logout(
        response: Response,
        request: Request,
        db: Database = Depends(_db),
        settings: Settings = Depends(_settings),
    ) -> dict[str, Any]:
        session_id = request.cookies.get(settings.session_cookie_name)
        if session_id:
            db.delete_session(session_id)
        response.delete_cookie(settings.session_cookie_name, path="/")
        request.state.guest_id = uuid4().hex
        request.state.guest_owner_id = f"guest:{request.state.guest_id}"
        return {"ok": True}

    @app.get("/api/config")
    async def get_config(
        viewer: ViewerContext = Depends(_viewer),
        db: Database = Depends(_db),
        settings: Settings = Depends(_settings),
    ) -> dict[str, Any]:
        return _public_config(
            db.get_config(viewer.owner_id, settings, user_name=_viewer_name(viewer, settings)),
            viewer,
        )

    @app.put("/api/config")
    async def update_config(
        payload: ConfigUpdate,
        viewer: ViewerContext = Depends(_viewer),
        db: Database = Depends(_db),
        settings: Settings = Depends(_settings),
    ) -> dict[str, Any]:
        updates = payload.model_dump(exclude_unset=True)
        clear_api_key = bool(updates.pop("clear_api_key", False))
        if viewer.authenticated:
            locked = {"base_url", "usage_path", "user_name", "managed_by_auth"}
            if clear_api_key or locked.intersection(updates):
                if locked.intersection(updates):
                    raise HTTPException(status_code=403, detail="Signed-in accounts use a fixed JokoAI endpoint and profile")
        if clear_api_key:
            updates["api_key"] = ""
        elif "api_key" in updates and updates["api_key"] == "":
            updates.pop("api_key")
        if "base_url" in updates and updates["base_url"]:
            updates["base_url"] = updates["base_url"].rstrip("/")
        config = db.update_config(viewer.owner_id, settings, updates)
        return _public_config(config, viewer)

    @app.post("/api/config/test")
    async def test_config(
        viewer: ViewerContext = Depends(_viewer),
        db: Database = Depends(_db),
        settings: Settings = Depends(_settings),
        provider: OpenAICompatibleImageClient = Depends(_provider),
    ) -> dict[str, Any]:
        try:
            config = db.get_config(viewer.owner_id, settings, user_name=_viewer_name(viewer, settings))
            return await provider.test_connection(config)
        except ProviderError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    @app.get("/api/account")
    async def account(
        viewer: ViewerContext = Depends(_viewer),
        db: Database = Depends(_db),
        settings: Settings = Depends(_settings),
        provider: OpenAICompatibleImageClient = Depends(_provider),
    ) -> dict[str, Any]:
        config = db.get_config(viewer.owner_id, settings, user_name=_viewer_name(viewer, settings))
        usage = await _safe_usage(provider, config)
        return {
            "viewer": _viewer_payload(viewer, config),
            "user": {
                "name": config["user_name"],
                "email": viewer.user["email"] if viewer.user else None,
                "username": viewer.user["username"] if viewer.user else None,
                "role": viewer.user["role"] if viewer.user else None,
                "authenticated": viewer.authenticated,
                "guest": not viewer.authenticated,
                "api_key_set": bool(config["api_key"]),
                "api_key_source": config["api_key_source"],
                "model": config["model"],
            },
            "balance": usage,
            "stats": db.stats(viewer.owner_id),
        }

    @app.get("/api/balance")
    async def balance(
        viewer: ViewerContext = Depends(_viewer),
        db: Database = Depends(_db),
        settings: Settings = Depends(_settings),
        provider: OpenAICompatibleImageClient = Depends(_provider),
    ) -> dict[str, Any]:
        config = db.get_config(viewer.owner_id, settings, user_name=_viewer_name(viewer, settings))
        return await _safe_usage(provider, config)

    @app.get("/api/ledger")
    async def ledger(
        limit: int = 20,
        viewer: ViewerContext = Depends(_viewer),
        db: Database = Depends(_db),
    ) -> dict[str, Any]:
        return {"items": db.list_ledger(viewer.owner_id, limit)}

    @app.get("/api/history")
    async def history(
        limit: int = 30,
        offset: int = 0,
        q: str = "",
        viewer: ViewerContext = Depends(_viewer),
        db: Database = Depends(_db),
    ) -> dict[str, Any]:
        return {"items": db.list_history(viewer.owner_id, limit=limit, offset=offset, q=q)}

    @app.get("/api/inspirations")
    async def inspirations(
        limit: int = 48,
        offset: int = 0,
        q: str = "",
        section: str = "",
        viewer: ViewerContext = Depends(_viewer),
        db: Database = Depends(_db),
    ) -> dict[str, Any]:
        favorite_owner_id = viewer.owner_id if viewer.authenticated else None
        return {
            "items": db.list_inspirations(
                limit=limit,
                offset=offset,
                q=q,
                section=section,
                favorite_owner_id=favorite_owner_id,
            ),
            "total": db.count_inspirations(q=q, section=section),
            "limit": limit,
            "offset": offset,
        }

    @app.get("/api/inspirations/favorites")
    async def favorite_inspirations(
        limit: int = 48,
        offset: int = 0,
        q: str = "",
        section: str = "",
        viewer: ViewerContext = Depends(_viewer),
        db: Database = Depends(_db),
    ) -> dict[str, Any]:
        _require_authenticated(viewer)
        return {
            "items": db.list_inspirations(
                limit=limit,
                offset=offset,
                q=q,
                section=section,
                favorite_owner_id=viewer.owner_id,
                favorites_only=True,
            ),
            "total": db.count_inspirations(
                q=q,
                section=section,
                favorite_owner_id=viewer.owner_id,
                favorites_only=True,
            ),
            "limit": limit,
            "offset": offset,
        }

    @app.post("/api/inspirations/{inspiration_id}/favorite")
    async def favorite_inspiration(
        inspiration_id: str,
        viewer: ViewerContext = Depends(_viewer),
        db: Database = Depends(_db),
    ) -> dict[str, Any]:
        _require_authenticated(viewer)
        item = db.set_inspiration_favorite(viewer.owner_id, inspiration_id, True)
        if item is None:
            raise HTTPException(status_code=404, detail="Inspiration item not found")
        return {"ok": True, "item": item}

    @app.delete("/api/inspirations/{inspiration_id}/favorite")
    async def unfavorite_inspiration(
        inspiration_id: str,
        viewer: ViewerContext = Depends(_viewer),
        db: Database = Depends(_db),
    ) -> dict[str, Any]:
        _require_authenticated(viewer)
        item = db.set_inspiration_favorite(viewer.owner_id, inspiration_id, False)
        if item is None:
            raise HTTPException(status_code=404, detail="Inspiration item not found")
        return {"ok": True, "item": item}

    @app.get("/api/inspirations/stats")
    async def inspiration_stats(db: Database = Depends(_db)) -> dict[str, Any]:
        sources = db.get_site_settings().get("inspiration_sources") or [settings.inspiration_source_url]
        return {
            **db.inspiration_stats(),
            "source_url": sources[0] if sources else settings.inspiration_source_url,
            "source_urls": sources,
            "sync_interval_seconds": settings.inspiration_sync_interval_seconds,
            "last_sync": app.state.last_inspiration_sync,
            "last_error": app.state.last_inspiration_sync_error,
        }

    @app.post("/api/inspirations/sync")
    async def inspiration_sync(
        db: Database = Depends(_db),
        settings: Settings = Depends(_settings),
    ) -> dict[str, Any]:
        try:
            result = await sync_inspirations(settings, db)
            app.state.last_inspiration_sync = result
            app.state.last_inspiration_sync_error = None
            return result
        except Exception as exc:
            app.state.last_inspiration_sync_error = str(exc)
            raise HTTPException(status_code=502, detail=f"Inspiration sync failed: {exc}") from exc

    @app.get("/api/history/{history_id}")
    async def history_detail(
        history_id: str,
        viewer: ViewerContext = Depends(_viewer),
        db: Database = Depends(_db),
    ) -> dict[str, Any]:
        record = db.get_history(viewer.owner_id, history_id)
        if record is None:
            raise HTTPException(status_code=404, detail="History item not found")
        return record

    @app.delete("/api/history/{history_id}")
    async def delete_history(
        history_id: str,
        viewer: ViewerContext = Depends(_viewer),
        db: Database = Depends(_db),
    ) -> dict[str, Any]:
        deleted = db.delete_history(viewer.owner_id, history_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="History item not found")
        return {"ok": True}

    @app.post("/api/history/{history_id}/publish")
    async def publish_history(
        history_id: str,
        viewer: ViewerContext = Depends(_viewer),
        db: Database = Depends(_db),
        settings: Settings = Depends(_settings),
    ) -> dict[str, Any]:
        try:
            inspiration = db.publish_history_as_inspiration(
                viewer.owner_id,
                history_id,
                author=_viewer_name(viewer, settings),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if inspiration is None:
            raise HTTPException(status_code=404, detail="History item not found")
        item = db.get_history(viewer.owner_id, history_id)
        return {"ok": True, "item": item, "inspiration": inspiration}

    @app.delete("/api/history/{history_id}/publish")
    async def unpublish_history(
        history_id: str,
        viewer: ViewerContext = Depends(_viewer),
        db: Database = Depends(_db),
    ) -> dict[str, Any]:
        history_item = db.get_history(viewer.owner_id, history_id)
        if history_item is None:
            raise HTTPException(status_code=404, detail="History item not found")
        db.unpublish_history_inspiration(viewer.owner_id, history_id)
        item = db.get_history(viewer.owner_id, history_id)
        return {"ok": True, "item": item}

    @app.get("/api/tasks/{task_id}")
    async def image_task_status(
        task_id: str,
        viewer: ViewerContext = Depends(_viewer),
        db: Database = Depends(_db),
    ) -> dict[str, Any]:
        task = db.get_image_task(viewer.owner_id, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")
        return _public_image_task(db, viewer.owner_id, task)

    @app.get("/api/tasks")
    async def image_tasks(
        limit: int = 20,
        status: str = "",
        viewer: ViewerContext = Depends(_viewer),
        db: Database = Depends(_db),
    ) -> dict[str, Any]:
        allowed_statuses = {"queued", "running", "succeeded", "failed"}
        statuses = [item.strip() for item in status.split(",") if item.strip()]
        invalid_statuses = [item for item in statuses if item not in allowed_statuses]
        if invalid_statuses:
            raise HTTPException(status_code=400, detail=f"Unsupported task status filter: {', '.join(invalid_statuses)}")
        tasks = db.list_image_tasks(viewer.owner_id, limit=limit, statuses=statuses or None)
        return {"items": [_public_image_task(db, viewer.owner_id, task) for task in tasks]}

    @app.post("/api/prompts/optimize")
    async def optimize_prompt(
        request: PromptOptimizeRequest,
        viewer: ViewerContext = Depends(_viewer),
        db: Database = Depends(_db),
        settings: Settings = Depends(_settings),
        provider: OpenAICompatibleImageClient = Depends(_provider),
    ) -> dict[str, Any]:
        config = db.get_config(viewer.owner_id, settings, user_name=_viewer_name(viewer, settings))
        payload = _prompt_optimizer_payload(request, settings)
        try:
            provider_response = await provider.chat_completion(config, payload)
        except ProviderError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
        optimized_prompt = _extract_chat_completion_text(provider_response)
        if not optimized_prompt:
            raise HTTPException(status_code=502, detail="Prompt optimizer returned an empty response")
        return {
            "prompt": optimized_prompt,
            "original_prompt": request.prompt,
            "instruction": request.instruction or "",
            "model": payload["model"],
            "usage": provider_response.get("usage") if isinstance(provider_response, dict) else None,
        }

    @app.post("/api/images/generate")
    async def generate_image(
        request: GenerateRequest,
        raw_request: Request,
        viewer: ViewerContext = Depends(_viewer),
        db: Database = Depends(_db),
        settings: Settings = Depends(_settings),
    ) -> dict[str, Any]:
        config = db.get_config(viewer.owner_id, settings, user_name=_viewer_name(viewer, settings))
        payload = _image_payload(config, request)
        task = db.create_image_task(
            viewer.owner_id,
            {
                "mode": "generate",
                "prompt": request.prompt,
                "model": payload["model"],
                "size": payload["size"],
                "aspect_ratio": request.aspect_ratio or "",
                "quality": payload["quality"],
                "request": payload,
            },
        )
        _schedule_image_task(raw_request.app, task["id"])
        return _public_image_task(db, viewer.owner_id, task)

    @app.post("/api/images/edit")
    async def edit_image(
        prompt: Annotated[str, Form(min_length=1, max_length=8000)],
        image: Annotated[list[UploadFile], File()],
        raw_request: Request,
        mask: Annotated[UploadFile | None, File()] = None,
        model: Annotated[str | None, Form()] = None,
        size: Annotated[str | None, Form()] = None,
        aspect_ratio: Annotated[str | None, Form()] = None,
        quality: Annotated[str | None, Form()] = None,
        n: Annotated[int, Form(ge=1, le=4)] = 1,
        viewer: ViewerContext = Depends(_viewer),
        db: Database = Depends(_db),
        settings: Settings = Depends(_settings),
    ) -> dict[str, Any]:
        config = db.get_config(viewer.owner_id, settings, user_name=_viewer_name(viewer, settings))
        saved_uploads = [await save_upload(settings, upload) for upload in image]
        saved_mask = await save_upload(settings, mask) if mask else None
        fields = {
            "model": model or config["model"],
            "prompt": prompt,
            "size": _provider_image_size(size or config["default_size"], aspect_ratio),
            "quality": quality or config["default_quality"],
            "n": str(n),
            "response_format": "b64_json",
        }
        task = db.create_image_task(
            viewer.owner_id,
            {
                "mode": "edit",
                "prompt": prompt,
                "model": fields["model"],
                "size": fields["size"],
                "aspect_ratio": aspect_ratio or "",
                "quality": fields["quality"],
                "request": {
                    "fields": fields,
                    "uploads": saved_uploads,
                    "mask": saved_mask,
                },
                "input_image_url": saved_uploads[0]["url"] if saved_uploads else None,
                "input_image_path": saved_uploads[0]["path"] if saved_uploads else None,
            },
        )
        _schedule_image_task(raw_request.app, task["id"])
        return _public_image_task(db, viewer.owner_id, task)

    return app


def _db() -> Database:
    raise RuntimeError("Dependency should be overridden by FastAPI")


def _settings() -> Settings:
    raise RuntimeError("Dependency should be overridden by FastAPI")


def _provider() -> OpenAICompatibleImageClient:
    raise RuntimeError("Dependency should be overridden by FastAPI")


def _auth_client() -> Sub2APIAuthClient:
    raise RuntimeError("Dependency should be overridden by FastAPI")


def _viewer(request: Request) -> ViewerContext:
    session = getattr(request.state, "viewer_session", None)
    guest_id = getattr(request.state, "guest_id", uuid4().hex)
    guest_owner_id = getattr(request.state, "guest_owner_id", f"guest:{guest_id}")
    return ViewerContext(
        owner_id=getattr(request.state, "viewer_owner_id", guest_owner_id),
        guest_owner_id=guest_owner_id,
        guest_id=guest_id,
        authenticated=session is not None,
        session_id=session["id"] if session else None,
        session=session,
    )


def _viewer_name(viewer: ViewerContext, settings: Settings) -> str:
    if viewer.user:
        return viewer.user.get("username") or viewer.user.get("email") or settings.user_name
    return settings.user_name


def _require_admin(viewer: ViewerContext) -> None:
    if not viewer.authenticated:
        raise HTTPException(status_code=401, detail="Authentication required")
    if not viewer.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")


def _require_authenticated(viewer: ViewerContext) -> None:
    if not viewer.authenticated:
        raise HTTPException(status_code=401, detail="Authentication required")


def _public_site_settings(settings_data: dict[str, Any], viewer: ViewerContext, settings: Settings) -> dict[str, Any]:
    payload = {
        "default_locale": settings_data["default_locale"],
        "announcement": {
            "enabled": bool(settings_data["announcement_enabled"]),
            "title": settings_data["announcement_title"],
            "body": settings_data["announcement_body"],
            "updated_at": settings_data["announcement_updated_at"],
        },
        "inspiration_sources": settings_data.get("inspiration_sources") or [],
        "viewer": {
            "authenticated": viewer.authenticated,
            "is_admin": viewer.is_admin,
        },
    }
    if viewer.is_admin:
        payload["upstream"] = {
            "provider_base_url": str(settings_data.get("provider_base_url") or ""),
            "auth_base_url": str(settings_data.get("auth_base_url") or ""),
            "effective_provider_base_url": _effective_provider_base_url(settings_data, settings),
            "effective_auth_base_url": _effective_auth_base_url(settings_data, settings),
        }
    return payload


def _normalize_upstream_url(value: Any) -> str:
    text = str(value or "").strip().rstrip("/")
    if not text:
        return ""
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=400, detail="Upstream URL must be a valid http:// or https:// URL")
    return text


def _effective_provider_base_url(settings_data: dict[str, Any], settings: Settings) -> str:
    return str(settings_data.get("provider_base_url") or settings.provider_base_url).strip().rstrip("/")


def _effective_auth_base_url(settings_data: dict[str, Any], settings: Settings) -> str:
    return str(settings_data.get("auth_base_url") or settings.auth_base_url).strip().rstrip("/")


def _site_auth_base_url(db: Database, settings: Settings) -> str:
    return _effective_auth_base_url(db.get_site_settings(), settings)


def _site_provider_base_url(db: Database, settings: Settings) -> str:
    return _effective_provider_base_url(db.get_site_settings(), settings)


def _public_config(config: dict[str, Any], viewer: ViewerContext) -> dict[str, Any]:
    managed = bool(config.get("managed_by_auth"))
    return {
        "owner_id": config["owner_id"],
        "model": config["model"],
        "default_size": config["default_size"],
        "default_quality": config["default_quality"],
        "user_name": config["user_name"],
        "managed_by_auth": managed,
        "api_key_set": bool(config["api_key"]),
        "api_key_hint": _mask_key(config["api_key"]),
        "api_key_source": config["api_key_source"],
        "api_key_editable": True,
        "authenticated": viewer.authenticated,
    }


def _viewer_payload(viewer: ViewerContext, config: dict[str, Any]) -> dict[str, Any]:
    return {
        "authenticated": viewer.authenticated,
        "owner_id": viewer.owner_id,
        "guest_id": viewer.guest_id,
        "api_key_source": config["api_key_source"],
        "user": viewer.user,
    }


def _mask_key(api_key: str) -> str:
    if not api_key:
        return ""
    if len(api_key) <= 10:
        return f"{api_key[:2]}***{api_key[-2:]}"
    return f"{api_key[:6]}...{api_key[-4:]}"


def _set_guest_cookie(response: Response, settings: Settings, guest_id: str) -> None:
    response.set_cookie(
        settings.guest_cookie_name,
        guest_id,
        max_age=settings.guest_ttl_seconds,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/",
    )


def _set_session_cookie(response: Response, settings: Settings, session_id: str) -> None:
    response.set_cookie(
        settings.session_cookie_name,
        session_id,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/",
    )


async def _complete_auth_flow(
    db: Database,
    settings: Settings,
    auth_client: Sub2APIAuthClient,
    request: Request,
    response: Response,
    auth_result: dict[str, Any],
) -> dict[str, Any]:
    access_token = str(auth_result.get("access_token") or "").strip()
    user = auth_result.get("user")
    if not access_token or not isinstance(user, dict):
        raise HTTPException(status_code=502, detail="JokoAI login response was missing user credentials")

    user_id = int(user["id"])
    owner_id = f"user:{user_id}"
    display_name = str(user.get("username") or user.get("email") or f"user-{user_id}")
    auth_base_url = _site_auth_base_url(db, settings)
    provider_base_url = _site_provider_base_url(db, settings)
    api_key = await _resolve_user_api_key(auth_client, auth_base_url, access_token)

    db.merge_owner_data(
        request.state.guest_owner_id,
        owner_id,
        settings,
        user_name=display_name,
    )
    config = db.apply_managed_config(
        owner_id,
        settings,
        api_key=api_key,
        user_name=display_name,
        base_url=provider_base_url,
    )
    session = db.create_session(
        owner_id=owner_id,
        sub2api_user_id=user_id,
        email=str(user.get("email") or ""),
        username=str(user.get("username") or ""),
        role=str(user.get("role") or "user"),
        ttl_seconds=settings.session_ttl_seconds,
        access_token=access_token,
        refresh_token=str(auth_result.get("refresh_token") or ""),
        user_agent=request.headers.get("user-agent"),
        ip_address=_client_ip(request),
    )
    new_guest_id = uuid4().hex
    request.state.guest_id = new_guest_id
    request.state.guest_owner_id = f"guest:{new_guest_id}"
    _set_session_cookie(response, settings, session["id"])
    return _viewer_payload(
        ViewerContext(
            owner_id=owner_id,
            guest_owner_id=request.state.guest_owner_id,
            guest_id=request.state.guest_id,
            authenticated=True,
            session_id=session["id"],
            session=session,
        ),
        config,
    )


async def _resolve_user_api_key(
    auth_client: Sub2APIAuthClient,
    auth_base_url: str,
    access_token: str,
) -> str:
    keys = await auth_client.list_keys(auth_base_url, access_token)
    selected = _select_existing_key(keys)
    if selected and selected.get("key"):
        return str(selected["key"])

    payload: dict[str, Any] = {"name": "cybergen-image"}
    created = await auth_client.create_key(auth_base_url, access_token, payload)
    key = str(created.get("key") or "").strip()
    if not key:
        raise HTTPException(status_code=502, detail="JokoAI did not return a usable API key")
    return key


def _select_existing_key(keys: list[dict[str, Any]]) -> dict[str, Any] | None:
    def sort_key(item: dict[str, Any]) -> tuple[int, int]:
        status = 0 if item.get("status") == "active" else 1
        group = item.get("group") if isinstance(item.get("group"), dict) else {}
        platform = 0 if group.get("platform") == "openai" else 1
        return status, platform

    candidates = [item for item in keys if isinstance(item.get("key"), str) and item.get("key")]
    if not candidates:
        return None
    return sorted(candidates, key=sort_key)[0]


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    if request.client:
        return request.client.host
    return None


def _image_payload(config: dict[str, Any], request: GenerateRequest) -> dict[str, Any]:
    payload = {
        "model": request.model or config["model"],
        "prompt": request.prompt,
        "size": _provider_image_size(request.size or config["default_size"], request.aspect_ratio),
        "quality": request.quality or config["default_quality"],
        "n": request.n,
        "response_format": "b64_json",
    }
    if request.background:
        payload["background"] = request.background
    if request.output_format:
        payload["output_format"] = request.output_format
    return payload


def _prompt_optimizer_payload(request: PromptOptimizeRequest, settings: Settings) -> dict[str, Any]:
    context_lines = [
        f"原始提示词：\n{request.prompt.strip()}",
    ]
    if request.instruction and request.instruction.strip():
        context_lines.append(f"修改要求：\n{request.instruction.strip()}")
    output_options = []
    if request.aspect_ratio:
        output_options.append(f"比例 {request.aspect_ratio}")
    if request.size:
        output_options.append(f"尺寸 {request.size}")
    if request.quality:
        output_options.append(f"质量 {request.quality}")
    if output_options:
        context_lines.append(f"当前生图参数：{'，'.join(output_options)}")
    context_lines.append("请返回优化后的最终提示词。")
    return {
        "model": (request.model or settings.prompt_optimizer_model).strip(),
        "messages": [
            {"role": "system", "content": PROMPT_OPTIMIZER_SYSTEM_PROMPT},
            {"role": "user", "content": "\n\n".join(context_lines)},
        ],
        "temperature": 0.55,
        "max_tokens": 1800,
        "stream": False,
    }


def _extract_chat_completion_text(provider_response: dict[str, Any]) -> str:
    choices = provider_response.get("choices") if isinstance(provider_response, dict) else None
    if not isinstance(choices, list) or not choices:
        return ""
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content.strip().strip('"').strip("'").strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "\n".join(parts).strip().strip('"').strip("'").strip()
    return ""


def _provider_image_size(size: str, aspect_ratio: str | None = None) -> str:
    cleaned_size = str(size or "").strip()
    scale = cleaned_size.upper()
    ratio = str(aspect_ratio or "1:1").strip() or "1:1"
    if scale in SIZE_PRESETS:
        if ratio not in SIZE_PRESETS[scale]:
            raise HTTPException(status_code=400, detail=f"Unsupported image size combination: {scale} {ratio}")
        return SIZE_PRESETS[scale][ratio]
    dimension_parts = cleaned_size.lower().split("x")
    if len(dimension_parts) == 2 and all(part.isdigit() for part in dimension_parts):
        width, height = (int(part) for part in dimension_parts)
        if width * height < 1024 * 1024:
            raise HTTPException(status_code=400, detail=f"Unsupported image size below minimum pixel budget: {cleaned_size}")
        if width % 16 != 0 or height % 16 != 0:
            raise HTTPException(status_code=400, detail=f"Unsupported image size, width and height must be divisible by 16: {cleaned_size}")
        if max(width, height) > 3840:
            raise HTTPException(status_code=400, detail=f"Unsupported image size: {cleaned_size}")
        if width == height and width > 2048:
            raise HTTPException(status_code=400, detail=f"Unsupported image size: {cleaned_size}")
    return cleaned_size


def _image_size_tier(size: str) -> str:
    cleaned_size = str(size or "").strip().lower()
    if cleaned_size in SIZE_TIER_BY_DIMENSION:
        return SIZE_TIER_BY_DIMENSION[cleaned_size]
    parts = cleaned_size.split("x")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        tier = cleaned_size.upper()
        if tier in {"1K", "2K", "4K"}:
            return tier
        return "2K"
    width, height = (int(part) for part in parts)
    pixels = width * height
    if pixels <= 1_400_000:
        return "1K"
    if pixels <= 4_300_000:
        return "2K"
    return "4K"


def _image_ledger_amount(settings: Settings, size: str) -> float:
    tier = _image_size_tier(size)
    if tier == "1K":
        return settings.image_price_1k
    if tier == "4K":
        return settings.image_price_4k
    return settings.image_price_2k


def _backfill_zero_amount_ledger(db: Database, settings: Settings) -> int:
    updated = 0
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT le.id, le.metadata_json, ih.size, ih.aspect_ratio, ih.quality, ih.usage_json
            FROM ledger_entries le
            JOIN image_history ih ON ih.id = le.history_id
            WHERE le.amount = 0
              AND le.event_type IN ('generate', 'edit')
              AND ih.status = 'succeeded'
            """
        ).fetchall()
        for row in rows:
            amount = _image_ledger_amount(settings, row["size"])
            if amount <= 0:
                continue
            metadata = _json_object(row["metadata_json"])
            metadata.update(
                {
                    "size": row["size"],
                    "aspect_ratio": row["aspect_ratio"],
                    "quality": row["quality"],
                    "size_tier": _image_size_tier(row["size"]),
                    "cost_source": "local_image_price_backfill",
                    "usage": _json_object(row["usage_json"]),
                }
            )
            conn.execute(
                """
                UPDATE ledger_entries
                SET amount = ?, metadata_json = ?
                WHERE id = ?
                """,
                (amount, json.dumps(metadata, ensure_ascii=False), row["id"]),
            )
            updated += 1
    return updated


def _json_object(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if not raw:
        return {}
    try:
        value = json.loads(str(raw))
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _provider_response_image_count(provider_response: dict[str, Any]) -> int:
    data = provider_response.get("data")
    if not isinstance(data, list):
        return 1
    return max(1, len([item for item in data if isinstance(item, dict)]))


async def _resolve_image_ledger_cost(
    db: Database,
    settings: Settings,
    auth_client: Sub2APIAuthClient,
    *,
    owner_id: str,
    config: dict[str, Any],
    model: str,
    size: str,
    image_count: int,
) -> ImageLedgerCost:
    if config.get("api_key_source") == "managed":
        actual = await _sub2api_actual_image_ledger_cost(
            db,
            settings,
            auth_client,
            owner_id=owner_id,
            model=model,
            image_count=image_count,
        )
        if actual is not None:
            return actual
    return ImageLedgerCost(amount=_image_ledger_amount(settings, size), source="local_image_price")


async def _sub2api_actual_image_ledger_cost(
    db: Database,
    settings: Settings,
    auth_client: Sub2APIAuthClient,
    *,
    owner_id: str,
    model: str,
    image_count: int,
) -> ImageLedgerCost | None:
    session = db.latest_session_for_owner(owner_id)
    access_token = str((session or {}).get("access_token") or "").strip()
    if not access_token:
        return None

    params = {
        "page": 1,
        "page_size": 10,
        "sort_by": "created_at",
        "sort_order": "desc",
        "model": model,
    }
    for attempt in range(5):
        try:
            logs = await auth_client.list_usage(_site_auth_base_url(db, settings), access_token, params)
        except ProviderError:
            return None
        usage_log = _select_sub2api_image_usage_log(logs, model)
        if usage_log is not None:
            total_cost = _float_or_none(usage_log.get("actual_cost"))
            if total_cost is None:
                total_cost = _float_or_none(usage_log.get("total_cost"))
            if total_cost is not None:
                divisor = max(1, int(usage_log.get("image_count") or image_count or 1))
                return ImageLedgerCost(
                    amount=round(max(0.0, total_cost) / divisor, 8),
                    source="sub2api_actual_cost",
                    usage_log=_compact_sub2api_usage_log(usage_log),
                )
        if attempt < 4:
            await asyncio.sleep(0.3)
    return None


def _select_sub2api_image_usage_log(logs: list[dict[str, Any]], model: str) -> dict[str, Any] | None:
    expected_model = str(model or "").strip().lower()
    for item in logs:
        if expected_model and str(item.get("model") or "").strip().lower() != expected_model:
            continue
        inbound_endpoint = str(item.get("inbound_endpoint") or "")
        upstream_endpoint = str(item.get("upstream_endpoint") or "")
        is_image = bool(item.get("image_count")) or bool(item.get("image_size")) or "images/" in inbound_endpoint or "images/" in upstream_endpoint
        if is_image:
            return item
    return None


def _compact_sub2api_usage_log(item: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "id",
        "request_id",
        "model",
        "actual_cost",
        "total_cost",
        "image_count",
        "image_size",
        "billing_mode",
        "created_at",
    ]
    return {key: item.get(key) for key in keys if key in item}


def _float_or_none(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _public_image_task(db: Database, owner_id: str, task: dict[str, Any]) -> dict[str, Any]:
    history_ids = task.get("result_history_ids") or []
    return {
        "id": task["id"],
        "owner_id": task["owner_id"],
        "mode": task["mode"],
        "prompt": task["prompt"],
        "model": task["model"],
        "size": task["size"],
        "aspect_ratio": task.get("aspect_ratio") or "",
        "quality": task["quality"],
        "status": task["status"],
        "error": task.get("error"),
        "created_at": task["created_at"],
        "updated_at": task["updated_at"],
        "started_at": task.get("started_at"),
        "completed_at": task.get("completed_at"),
        "items": db.get_history_items(owner_id, history_ids),
        "result": task.get("result"),
    }


def _schedule_image_task(app: FastAPI, task_id: str) -> None:
    existing = app.state.image_tasks.get(task_id)
    if existing is not None and not existing.done():
        return
    task = asyncio.create_task(_run_image_task(app, task_id))
    app.state.image_tasks[task_id] = task

    def _cleanup(done_task: asyncio.Task[Any]) -> None:
        app.state.image_tasks.pop(task_id, None)
        try:
            done_task.result()
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    task.add_done_callback(_cleanup)


def _load_saved_upload(upload: dict[str, Any]) -> tuple[str, bytes, str]:
    path = Path(str(upload["path"]))
    return (
        str(upload.get("filename") or path.name),
        path.read_bytes(),
        str(upload.get("content_type") or "application/octet-stream"),
    )


async def _run_image_task(app: FastAPI, task_id: str) -> None:
    db: Database = app.state.db
    settings: Settings = app.state.settings
    provider: OpenAICompatibleImageClient = app.state.provider
    auth_client: Sub2APIAuthClient = app.state.auth_client

    task = db.get_image_task_by_id(task_id)
    if task is None:
        return

    db.update_image_task(
        task_id,
        {
            "status": "running",
            "started_at": task.get("started_at") or utc_now(),
            "error": None,
        },
    )
    task = db.get_image_task_by_id(task_id)
    if task is None:
        return

    request_payload = task.get("request") or {}
    owner_id = task["owner_id"]
    config = db.get_config(owner_id, settings)

    try:
        if task["mode"] == "generate":
            if not isinstance(request_payload, dict):
                raise ValueError("Generate task payload was missing")
            provider_response = await _call_provider_with_retries(
                lambda: provider.generate_image(config, request_payload)
            )
        elif task["mode"] == "edit":
            if not isinstance(request_payload, dict):
                raise ValueError("Edit task payload was missing")
            fields = request_payload.get("fields")
            uploads = request_payload.get("uploads")
            if not isinstance(fields, dict) or not isinstance(uploads, list):
                raise ValueError("Edit task payload was incomplete")
            image_files = [_load_saved_upload(item) for item in uploads]
            if not image_files:
                raise ValueError("Edit task is missing source images")
            saved_mask = request_payload.get("mask")
            mask_file = _load_saved_upload(saved_mask) if isinstance(saved_mask, dict) else None
            provider_response = await _call_provider_with_retries(
                lambda: provider.edit_image(config, fields, image_files, mask_file)
            )
        else:
            raise ValueError(f"Unsupported task mode: {task['mode']}")

        latest_task = db.get_image_task_by_id(task_id) or task
        ledger_cost = await _resolve_image_ledger_cost(
            db,
            settings,
            auth_client,
            owner_id=latest_task["owner_id"],
            config=config,
            model=latest_task["model"],
            size=latest_task["size"],
            image_count=_provider_response_image_count(provider_response),
        )
        items = await _persist_image_response(
            db,
            settings,
            owner_id=latest_task["owner_id"],
            mode=latest_task["mode"],
            prompt=latest_task["prompt"],
            model=latest_task["model"],
            size=latest_task["size"],
            aspect_ratio=latest_task.get("aspect_ratio") or "",
            quality=latest_task["quality"],
            provider_response=provider_response,
            ledger_cost=ledger_cost,
            input_image_url=latest_task.get("input_image_url"),
            input_image_path=latest_task.get("input_image_path"),
        )
        db.update_image_task(
            task_id,
            {
                "status": "succeeded",
                "completed_at": utc_now(),
                "result_history_ids": [item["id"] for item in items],
                "result": {
                    "created": provider_response.get("created"),
                    "usage": provider_response.get("usage"),
                },
                "error": None,
            },
        )
    except asyncio.CancelledError:
        db.update_image_task(
            task_id,
            {
                "status": "failed",
                "error": "Task cancelled before completion",
                "completed_at": utc_now(),
            },
        )
        raise
    except ProviderError as exc:
        latest_task = db.get_image_task_by_id(task_id) or task
        failed = _record_failed_history(
            db,
            owner_id=latest_task["owner_id"],
            mode=latest_task["mode"],
            prompt=latest_task["prompt"],
            model=latest_task["model"],
            size=latest_task["size"],
            aspect_ratio=latest_task.get("aspect_ratio") or "",
            quality=latest_task["quality"],
            message=exc.message,
            provider_response=exc.payload,
            input_image_url=latest_task.get("input_image_url"),
            input_image_path=latest_task.get("input_image_path"),
        )
        db.update_image_task(
            task_id,
            {
                "status": "failed",
                "completed_at": utc_now(),
                "result_history_ids": [failed["id"]] if failed else [],
                "result": {"error": exc.message, "usage": None},
                "error": exc.message,
            },
        )
    except Exception as exc:
        latest_task = db.get_image_task_by_id(task_id) or task
        failed = _record_failed_history(
            db,
            owner_id=latest_task["owner_id"],
            mode=latest_task["mode"],
            prompt=latest_task["prompt"],
            model=latest_task["model"],
            size=latest_task["size"],
            aspect_ratio=latest_task.get("aspect_ratio") or "",
            quality=latest_task["quality"],
            message=str(exc),
            provider_response=None,
            input_image_url=latest_task.get("input_image_url"),
            input_image_path=latest_task.get("input_image_path"),
        )
        db.update_image_task(
            task_id,
            {
                "status": "failed",
                "completed_at": utc_now(),
                "result_history_ids": [failed["id"]] if failed else [],
                "result": {"error": str(exc), "usage": None},
                "error": str(exc),
            },
        )


async def _persist_image_response(
    db: Database,
    settings: Settings,
    *,
    owner_id: str,
    mode: str,
    prompt: str,
    model: str,
    size: str,
    aspect_ratio: str,
    quality: str,
    provider_response: dict[str, Any],
    ledger_cost: ImageLedgerCost,
    input_image_url: str | None = None,
    input_image_path: str | None = None,
) -> list[dict[str, Any]]:
    data = provider_response.get("data")
    if not isinstance(data, list) or not data:
        raise ValueError("Provider response did not contain image data")

    records = []
    for item in data:
        if not isinstance(item, dict):
            continue
        history_id = uuid4().hex
        saved = await save_provider_image(settings, history_id, item)
        record = db.create_history(
            owner_id,
            {
                "id": history_id,
                "mode": mode,
                "prompt": prompt,
                "model": model,
                "size": size,
                "aspect_ratio": aspect_ratio,
                "quality": quality,
                "status": "succeeded",
                "image_url": saved["url"],
                "image_path": saved["path"],
                "input_image_url": input_image_url,
                "input_image_path": input_image_path,
                "revised_prompt": item.get("revised_prompt"),
                "usage": provider_response.get("usage"),
                "provider_response": {"created": provider_response.get("created"), "source_url": saved.get("source_url")},
            },
        )
        db.add_ledger_entry(
            owner_id,
            {
                "event_type": mode,
                "amount": ledger_cost.amount,
                "description": f"{mode.upper()} {model}",
                "history_id": record["id"],
                "metadata": {
                    "size": size,
                    "aspect_ratio": aspect_ratio,
                    "quality": quality,
                    "size_tier": _image_size_tier(size),
                    "cost_source": ledger_cost.source,
                    "usage": provider_response.get("usage"),
                    "sub2api_usage_log": ledger_cost.usage_log,
                },
            },
        )
        records.append(record)
    if not records:
        raise ValueError("Provider response image data was empty")
    return records


async def _call_provider_with_retries(operation) -> dict[str, Any]:
    last_error: ProviderError | None = None
    for attempt in range(1, IMAGE_PROVIDER_MAX_ATTEMPTS + 1):
        try:
            return await operation()
        except ProviderError as exc:
            last_error = exc
            if attempt >= IMAGE_PROVIDER_MAX_ATTEMPTS or not _is_retryable_provider_error(exc):
                raise
            await asyncio.sleep(min(2 ** (attempt - 1), 4))
    if last_error is not None:
        raise last_error
    raise RuntimeError("Provider operation did not return a response")


def _is_retryable_provider_error(exc: ProviderError) -> bool:
    if exc.status_code not in RETRYABLE_PROVIDER_STATUS_CODES:
        return False
    payload = exc.payload
    if not isinstance(payload, dict):
        return True

    error = payload.get("error")
    if isinstance(error, dict):
        error_type = str(error.get("type") or "")
        message = str(error.get("message") or "")
    else:
        error_type = str(payload.get("type") or "")
        message = str(payload.get("message") or payload.get("error") or "")

    lowered = message.lower()
    if "insufficient" in lowered or "balance" in lowered:
        return False
    if error_type in {"upstream_error", "rate_limit_error", "server_error"}:
        return True
    return "upstream" in lowered or "temporarily unavailable" in lowered


def _record_failed_history(
    db: Database,
    owner_id: str,
    mode: str,
    prompt: str,
    model: str,
    size: str,
    aspect_ratio: str,
    quality: str,
    message: str,
    provider_response: Any | None,
    input_image_url: str | None = None,
    input_image_path: str | None = None,
) -> dict[str, Any]:
    return db.create_history(
        owner_id,
        {
            "mode": mode,
            "prompt": prompt,
            "model": model,
            "size": size,
            "aspect_ratio": aspect_ratio,
            "quality": quality,
            "status": "failed",
            "input_image_url": input_image_url,
            "input_image_path": input_image_path,
            "error": message,
            "provider_response": provider_response,
        },
    )


async def _safe_usage(provider: OpenAICompatibleImageClient, config: dict[str, Any]) -> dict[str, Any]:
    if not config.get("api_key"):
        return {"ok": False, "remaining": None, "message": "API Key not configured", "raw": None}
    try:
        return await provider.usage(config)
    except ProviderError as exc:
        return {"ok": False, "remaining": None, "message": exc.message, "raw": exc.payload}


app = create_app()
