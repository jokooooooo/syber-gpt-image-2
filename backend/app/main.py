from __future__ import annotations

import asyncio
import base64
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
from pydantic import BaseModel, Field, ValidationError

from .auth_client import Sub2APIAuthClient
from .db import Database, utc_now
from .inspirations import normalize_inspiration_source_urls, run_inspiration_sync_loop, sync_inspirations
from .provider import OpenAICompatibleImageClient, ProviderError
from .settings import Settings
from .storage import load_stored_image_as_upload, save_provider_image, save_upload


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
    n: int = Field(default=1, ge=1, le=9)
    background: str | None = None
    output_format: str | None = None


class HistoryEditRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=8000)
    model: str | None = Field(default=None, max_length=128)
    size: str | None = Field(default=None, max_length=64)
    aspect_ratio: str | None = Field(default=None, max_length=32)
    quality: str | None = Field(default=None, max_length=32)
    reference_notes: list[dict[str, Any]] | None = None


class PromptOptimizeRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=12000)
    instruction: str | None = Field(default=None, max_length=2000)
    model: str | None = Field(default=None, max_length=120)
    size: str | None = Field(default=None, max_length=80)
    aspect_ratio: str | None = Field(default=None, max_length=20)
    quality: str | None = Field(default=None, max_length=40)


class InspirationAISearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    limit: int = Field(default=48, ge=1, le=96)
    offset: int = Field(default=0, ge=0)
    section: str | None = Field(default=None, max_length=200)
    model: str | None = Field(default=None, max_length=120)


class EcommercePublishCopyRequest(BaseModel):
    product_name: str = Field(default="", max_length=300)
    materials: str = Field(default="", max_length=1200)
    selling_points: str = Field(default="", max_length=1600)
    scenarios: str = Field(default="", max_length=1200)
    platform: str = Field(default="", max_length=120)
    style: str = Field(default="", max_length=800)
    extra_requirements: str = Field(default="", max_length=1600)
    image_count: int = Field(default=1, ge=1, le=9)
    size: str | None = Field(default=None, max_length=80)
    aspect_ratio: str | None = Field(default=None, max_length=20)
    model: str | None = Field(default=None, max_length=120)


class PaymentCreateOrderRequest(BaseModel):
    amount: float = Field(gt=0)
    payment_type: str = Field(min_length=1, max_length=80)
    order_type: str = Field(default="balance", max_length=40)
    plan_id: int | None = Field(default=None, ge=1)


class PaymentVerifyOrderRequest(BaseModel):
    out_trade_no: str = Field(min_length=1, max_length=160)


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

SERIES_PROMPT_PLANNER_SYSTEM_PROMPT = """你是 JokoAI 的系列图像提示词规划师。
用户会提供一个总需求、生成模式、图片张数和画面参数。你的任务是把总需求拆解成一组同风格、同产品、可连续浏览的系列图像提示词。
要求：
1. 只输出 JSON，不要 Markdown、解释或代码块。
2. JSON 格式必须是：{"style_guide":"...", "items":[{"index":1,"title":"...","copy":"...","prompt":"..."}]}。
3. items 数量必须等于用户要求的图片张数，index 从 1 开始连续。
4. 每个 prompt 都必须可以独立用于 gpt-image-2 生图/改图接口。
5. 每个 prompt 都要包含统一风格约束：同一产品、同一色调、同一字体样式、同一标题/正文排版、同一电商详情页视觉系统。
6. 如果是电商详情页、海报组、主图/副图、故事分镜等需求，要自动拆成不同页面/屏幕/模块，不要重复同一张图。
7. 如果是改图模式，prompt 必须明确要求严格参考上传图片中的主体、材质、结构和外观，只改变本屏需要表达的场景、文案和布局。
8. 画面中文字必须简洁、清晰、可读，避免乱码；标题和说明文案由你在 copy 字段中给出，并写入对应 prompt。
9. 保持原提示词主要语言；中文输入输出中文，英文输入输出英文。"""

ECOMMERCE_PRODUCT_ANALYZER_SYSTEM_PROMPT = """你是 JokoAI 的电商商品图识别分析师。
用户会上传一张或多张商品参考图，并提供商品名称、材质、卖点、平台和风格。你的任务是综合识别商品外观并输出可用于后续电商详情页生成的结构化信息。
要求：
1. 只输出 JSON，不要 Markdown、解释或代码块。
2. JSON 格式必须是：{"product_type":"...","appearance":"...","visible_material":"...","colors":["..."],"shape":"...","details":["..."],"generation_constraints":"..."}。
3. 如果有正面、侧面、背面、材质细节等多角度参考图，必须把它们合并理解为同一商品的完整外观，不得只依据第一张图。
4. generation_constraints 要明确说明生成时必须保持商品主体、颜色、材质、比例、结构、轮廓一致，并保留多角度参考图中可见的关键侧面/背面/细节信息。
5. 不确定的信息不要编造，优先根据图片可见信息和用户输入综合判断。
6. 中文输入输出中文，英文输入输出英文。"""

ECOMMERCE_PUBLISH_COPY_SYSTEM_PROMPT = """你是 JokoAI 的电商种草文案策划。
用户会提供一个已生成的电商详情页项目参数。你的任务是为小红书/朋友圈/社媒发布生成独立标题和正文。
要求：
1. 只输出 JSON，不要 Markdown、解释或代码块。
2. JSON 格式必须是：{"title":"...","body":"..."}。
3. 标题要像真实小红书标题，30 字以内，有商品主题和卖点，不要标题党。
4. 正文 120-260 字，口吻自然，适合发布图文，不要说“根据提示词”“AI 生成了”等工具过程。
5. 正文要结合商品名称、材质、卖点、场景、风格，突出使用感、场景感和购买理由。
6. 正文末尾带 4-8 个相关话题标签。
7. 如果用户字段为空，不要编造具体品牌、价格、功效认证或无法确认的信息。
8. 中文输入输出中文，英文输入输出英文。"""

INSPIRATION_AI_SEARCH_SYSTEM_PROMPT = """你是 JokoAI 的案例库搜索助手。
用户会用自然语言描述想找的图像案例。你的任务是把需求提炼成适合在标题、提示词、作者字段中检索的关键词。
要求：
1. 只输出 JSON，不要 Markdown、解释或代码块。
2. JSON 格式必须是：{"query":"...","keywords":["..."]}。
3. query 控制在 2-8 个关键词，使用空格分隔，优先保留主体、风格、用途、行业、画面类型和关键视觉元素。
4. 不要加入“帮我找”“案例”“图片”等无检索价值的词。
5. 中文输入优先输出中文关键词，英文输入优先输出英文关键词。"""


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
    sub2api_admin_token: str | None = Field(default=None, max_length=4096)
    sub2api_admin_jwt: str | None = Field(default=None, max_length=4096)
    recharge_url: str | None = Field(default=None, max_length=2048)


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
        for key in ("provider_base_url", "auth_base_url", "recharge_url"):
            if key in updates:
                updates[key] = _normalize_upstream_url(updates[key], label="Recharge URL" if key == "recharge_url" else "Upstream URL")
        for key in ("sub2api_admin_token", "sub2api_admin_jwt"):
            if key in updates:
                updates[key] = str(updates[key] or "").strip()
                if not updates[key]:
                    updates.pop(key)
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
                grant_trial=True,
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

    @app.get("/api/payment/checkout-info")
    async def payment_checkout_info(
        viewer: ViewerContext = Depends(_viewer),
        db: Database = Depends(_db),
        settings: Settings = Depends(_settings),
        auth_client: Sub2APIAuthClient = Depends(_auth_client),
    ) -> dict[str, Any]:
        access_token = _require_access_token(viewer)
        try:
            return await auth_client.payment_checkout_info(_site_auth_base_url(db, settings), access_token)
        except ProviderError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    @app.post("/api/payment/orders")
    async def payment_create_order(
        payload: PaymentCreateOrderRequest,
        viewer: ViewerContext = Depends(_viewer),
        db: Database = Depends(_db),
        settings: Settings = Depends(_settings),
        auth_client: Sub2APIAuthClient = Depends(_auth_client),
    ) -> dict[str, Any]:
        access_token = _require_access_token(viewer)
        body = payload.model_dump(exclude_none=True)
        try:
            return await auth_client.payment_create_order(_site_auth_base_url(db, settings), access_token, body)
        except ProviderError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    @app.get("/api/payment/orders/my")
    async def payment_list_orders(
        page: int = 1,
        page_size: int = 20,
        status: str = "",
        order_type: str = "",
        payment_type: str = "",
        viewer: ViewerContext = Depends(_viewer),
        db: Database = Depends(_db),
        settings: Settings = Depends(_settings),
        auth_client: Sub2APIAuthClient = Depends(_auth_client),
    ) -> dict[str, Any]:
        access_token = _require_access_token(viewer)
        params = {
            "page": max(1, page),
            "page_size": min(max(1, page_size), 100),
        }
        if status:
            params["status"] = status
        if order_type:
            params["order_type"] = order_type
        if payment_type:
            params["payment_type"] = payment_type
        try:
            return await auth_client.payment_list_orders(_site_auth_base_url(db, settings), access_token, params)
        except ProviderError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    @app.get("/api/payment/orders/{order_id}")
    async def payment_get_order(
        order_id: int,
        viewer: ViewerContext = Depends(_viewer),
        db: Database = Depends(_db),
        settings: Settings = Depends(_settings),
        auth_client: Sub2APIAuthClient = Depends(_auth_client),
    ) -> dict[str, Any]:
        access_token = _require_access_token(viewer)
        try:
            return await auth_client.payment_get_order(_site_auth_base_url(db, settings), access_token, order_id)
        except ProviderError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    @app.post("/api/payment/orders/{order_id}/cancel")
    async def payment_cancel_order(
        order_id: int,
        viewer: ViewerContext = Depends(_viewer),
        db: Database = Depends(_db),
        settings: Settings = Depends(_settings),
        auth_client: Sub2APIAuthClient = Depends(_auth_client),
    ) -> dict[str, Any]:
        access_token = _require_access_token(viewer)
        try:
            return await auth_client.payment_cancel_order(_site_auth_base_url(db, settings), access_token, order_id)
        except ProviderError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    @app.post("/api/payment/orders/verify")
    async def payment_verify_order(
        payload: PaymentVerifyOrderRequest,
        viewer: ViewerContext = Depends(_viewer),
        db: Database = Depends(_db),
        settings: Settings = Depends(_settings),
        auth_client: Sub2APIAuthClient = Depends(_auth_client),
    ) -> dict[str, Any]:
        access_token = _require_access_token(viewer)
        try:
            return await auth_client.payment_verify_order(_site_auth_base_url(db, settings), access_token, payload.out_trade_no)
        except ProviderError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

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
        viewer: ViewerContext = Depends(_viewer),
        db: Database = Depends(_db),
        settings: Settings = Depends(_settings),
    ) -> dict[str, Any]:
        _require_admin(viewer)
        try:
            result = await sync_inspirations(settings, db)
            app.state.last_inspiration_sync = result
            app.state.last_inspiration_sync_error = None
            return result
        except Exception as exc:
            app.state.last_inspiration_sync_error = str(exc)
            raise HTTPException(status_code=502, detail=f"Inspiration sync failed: {exc}") from exc

    @app.post("/api/inspirations/ai-search")
    async def inspirations_ai_search(
        request: InspirationAISearchRequest,
        viewer: ViewerContext = Depends(_viewer),
        db: Database = Depends(_db),
        settings: Settings = Depends(_settings),
        provider: OpenAICompatibleImageClient = Depends(_provider),
    ) -> dict[str, Any]:
        config = db.get_config(viewer.owner_id, settings, user_name=_viewer_name(viewer, settings))
        payload = _inspiration_ai_search_payload(request, settings)
        fallback_query = _fallback_inspiration_search_query(request.query)
        try:
            provider_response = await provider.chat_completion(config, payload)
            search_query = _extract_inspiration_search_query(_extract_chat_completion_text(provider_response), fallback_query)
        except ProviderError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
        section = (request.section or "").strip()
        return {
            "query": search_query,
            "original_query": request.query,
            "items": db.list_inspirations(
                limit=request.limit,
                offset=request.offset,
                q=search_query,
                section=section,
                favorite_owner_id=viewer.owner_id if viewer.authenticated else None,
            ),
            "total": db.count_inspirations(q=search_query, section=section),
            "limit": request.limit,
            "offset": request.offset,
            "model": payload["model"],
        }

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

    @app.post("/api/history/{history_id}/edit")
    async def edit_history_image(
        history_id: str,
        raw_request: Request,
        prompt: Annotated[str | None, Form()] = None,
        image: Annotated[list[UploadFile] | None, File()] = None,
        model: Annotated[str | None, Form()] = None,
        size: Annotated[str | None, Form()] = None,
        aspect_ratio: Annotated[str | None, Form()] = None,
        quality: Annotated[str | None, Form()] = None,
        reference_notes: Annotated[str | None, Form()] = None,
        viewer: ViewerContext = Depends(_viewer),
        db: Database = Depends(_db),
        settings: Settings = Depends(_settings),
    ) -> dict[str, Any]:
        _require_authenticated(viewer)
        edit_request = await _parse_history_edit_request(
            raw_request,
            prompt=prompt,
            model=model,
            size=size,
            aspect_ratio=aspect_ratio,
            quality=quality,
            reference_notes=reference_notes,
        )
        source = db.get_history(viewer.owner_id, history_id)
        if source is None:
            raise HTTPException(status_code=404, detail="History item not found")
        image_path = source.get("image_path")
        if not image_path or not Path(str(image_path)).exists():
            raise HTTPException(status_code=400, detail="History item has no stored image to edit")
        config = db.get_config(viewer.owner_id, settings, user_name=_viewer_name(viewer, settings))
        source_upload = load_stored_image_as_upload(str(image_path), source.get("image_url"))
        uploads: list[dict[str, Any]] = []
        original_upload: dict[str, Any] | None = None
        input_image_path = source.get("input_image_path")
        if input_image_path and str(input_image_path) != str(image_path) and Path(str(input_image_path)).exists():
            original_upload = {
                **load_stored_image_as_upload(str(input_image_path), source.get("input_image_url")),
                "reference_index": 0,
                "reference_role": "原商品主图",
                "reference_note": "商品身份、颜色、材质、结构和轮廓优先参考这张图。",
                "reference_primary": True,
            }
            uploads.append(original_upload)
        source_index = 1 if original_upload is not None else 0
        uploads.append(
            {
                **source_upload,
                "reference_index": source_index,
                "reference_role": "当前成品图",
                "reference_note": "版式、构图、文案层级、画面风格和待修改内容参考这张图。",
                "reference_primary": original_upload is None,
            }
        )
        extra_reference_notes = _normalize_reference_notes(edit_request.reference_notes, len(image or []))
        extra_uploads = _attach_reference_notes(
            [await save_upload(settings, upload) for upload in (image or [])],
            extra_reference_notes,
            start_index=source_index + 1,
        )
        uploads.extend(extra_uploads)
        provider_prompt = _history_edit_provider_prompt(
            edit_request.prompt,
            has_product_reference=original_upload is not None,
            extra_references=extra_uploads,
        )
        task_reference_notes = _task_reference_notes(uploads)
        primary_reference = original_upload or source_upload
        source_task_request = source.get("task_request")
        source_ecommerce = source_task_request.get("ecommerce") if isinstance(source_task_request, dict) else None
        fields = {
            "model": edit_request.model or source.get("model") or config["model"],
            "prompt": provider_prompt,
            "size": _provider_image_size(edit_request.size or source.get("size") or config["default_size"], edit_request.aspect_ratio or source.get("aspect_ratio") or None),
            "quality": edit_request.quality or source.get("quality") or config["default_quality"],
            "n": "1",
            "response_format": "b64_json",
        }
        task = db.create_image_task(
            viewer.owner_id,
            {
                "mode": "edit",
                "prompt": edit_request.prompt,
                "model": fields["model"],
                "size": fields["size"],
                "aspect_ratio": edit_request.aspect_ratio or source.get("aspect_ratio") or "",
                "quality": fields["quality"],
                "request": {
                    "fields": fields,
                    "uploads": uploads,
                    "mask": None,
                    "source_history_id": history_id,
                    "replace_history_id": history_id if isinstance(source_ecommerce, dict) else None,
                    "ecommerce": source_ecommerce if isinstance(source_ecommerce, dict) else None,
                    "reference_notes": task_reference_notes,
                },
                "input_image_url": primary_reference.get("url"),
                "input_image_path": primary_reference.get("path"),
            },
        )
        _schedule_image_task(raw_request.app, task["id"])
        return _public_image_task(db, viewer.owner_id, task)

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

    @app.post("/api/ecommerce/publish-copy")
    async def ecommerce_publish_copy(
        request: EcommercePublishCopyRequest,
        viewer: ViewerContext = Depends(_viewer),
        db: Database = Depends(_db),
        settings: Settings = Depends(_settings),
        provider: OpenAICompatibleImageClient = Depends(_provider),
    ) -> dict[str, Any]:
        config = db.get_config(viewer.owner_id, settings, user_name=_viewer_name(viewer, settings))
        payload = _ecommerce_publish_copy_payload(request, settings)
        try:
            provider_response = await provider.chat_completion(config, payload)
        except ProviderError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
        parsed = _extract_json_object(_extract_chat_completion_text(provider_response))
        if not isinstance(parsed, dict):
            raise HTTPException(status_code=502, detail="Publish copy generator returned an invalid response")
        title = str(parsed.get("title") or "").strip()
        body = str(parsed.get("body") or "").strip()
        if not title or not body:
            raise HTTPException(status_code=502, detail="Publish copy generator returned an empty response")
        return {
            "title": title,
            "body": body,
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
        _require_authenticated(viewer)
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
        n: Annotated[int, Form(ge=1, le=9)] = 1,
        reference_notes: Annotated[str | None, Form()] = None,
        viewer: ViewerContext = Depends(_viewer),
        db: Database = Depends(_db),
        settings: Settings = Depends(_settings),
    ) -> dict[str, Any]:
        _require_authenticated(viewer)
        config = db.get_config(viewer.owner_id, settings, user_name=_viewer_name(viewer, settings))
        normalized_reference_notes = _normalize_reference_notes(_parse_reference_notes(reference_notes), len(image))
        saved_uploads = _attach_reference_notes(
            [await save_upload(settings, upload) for upload in image],
            normalized_reference_notes,
        )
        saved_mask = await save_upload(settings, mask) if mask else None
        provider_prompt = _append_reference_notes_to_prompt(prompt, saved_uploads)
        fields = {
            "model": model or config["model"],
            "prompt": provider_prompt,
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
                    "reference_notes": _task_reference_notes(saved_uploads),
                },
                "input_image_url": saved_uploads[0]["url"] if saved_uploads else None,
                "input_image_path": saved_uploads[0]["path"] if saved_uploads else None,
            },
        )
        _schedule_image_task(raw_request.app, task["id"])
        return _public_image_task(db, viewer.owner_id, task)

    @app.post("/api/ecommerce/generate")
    async def ecommerce_generate(
        image: Annotated[UploadFile, File()],
        raw_request: Request,
        reference_image: Annotated[list[UploadFile] | None, File()] = None,
        product_name: Annotated[str, Form(max_length=300)] = "",
        materials: Annotated[str, Form(max_length=1200)] = "",
        selling_points: Annotated[str, Form(max_length=1600)] = "",
        scenarios: Annotated[str, Form(max_length=1200)] = "",
        platform: Annotated[str, Form(max_length=120)] = "",
        style: Annotated[str, Form(max_length=800)] = "",
        extra_requirements: Annotated[str, Form(max_length=1600)] = "",
        model: Annotated[str | None, Form()] = None,
        size: Annotated[str | None, Form()] = None,
        aspect_ratio: Annotated[str | None, Form()] = None,
        quality: Annotated[str | None, Form()] = None,
        n: Annotated[int, Form(ge=1, le=9)] = 4,
        reference_notes: Annotated[str | None, Form()] = None,
        viewer: ViewerContext = Depends(_viewer),
        db: Database = Depends(_db),
        settings: Settings = Depends(_settings),
        provider: OpenAICompatibleImageClient = Depends(_provider),
    ) -> dict[str, Any]:
        _require_authenticated(viewer)
        config = db.get_config(viewer.owner_id, settings, user_name=_viewer_name(viewer, settings))
        reference_upload_files = reference_image or []
        normalized_reference_notes = _normalize_reference_notes(_parse_reference_notes(reference_notes), 1 + len(reference_upload_files))
        saved_upload = _attach_reference_notes([await save_upload(settings, image)], normalized_reference_notes[:1])[0]
        extra_uploads = _attach_reference_notes(
            [await save_upload(settings, upload) for upload in reference_upload_files],
            normalized_reference_notes[1:],
            start_index=1,
        )
        ecommerce_uploads = [saved_upload, *extra_uploads]
        prompt = _ecommerce_prompt_from_fields(
            product_name=product_name,
            materials=materials,
            selling_points=selling_points,
            scenarios=scenarios,
            platform=platform,
            style=style,
            extra_requirements=extra_requirements,
            image_count=n,
        )
        analysis = await _analyze_ecommerce_product(
            provider,
            config,
            settings,
            upload=saved_upload,
            uploads=ecommerce_uploads,
            prompt=prompt,
        )
        provider_prompt = _append_reference_notes_to_prompt(_append_ecommerce_consistency_lock(prompt, analysis), ecommerce_uploads)
        fields = {
            "model": model or config["model"],
            "prompt": provider_prompt,
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
                    "uploads": ecommerce_uploads,
                    "mask": None,
                    "reference_notes": _task_reference_notes(ecommerce_uploads),
                    "ecommerce": {
                        "analysis": analysis,
                        "product_name": product_name,
                        "materials": materials,
                        "selling_points": selling_points,
                        "scenarios": scenarios,
                        "platform": platform,
                        "style": style,
                        "extra_requirements": extra_requirements,
                    },
                },
                "input_image_url": saved_upload["url"],
                "input_image_path": saved_upload["path"],
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


def _require_access_token(viewer: ViewerContext) -> str:
    _require_authenticated(viewer)
    access_token = str((viewer.session or {}).get("access_token") or "").strip()
    if not access_token:
        raise HTTPException(status_code=401, detail="JokoAI login token is missing")
    return access_token


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
        "recharge_url": _effective_recharge_url(settings_data, settings),
        "viewer": {
            "authenticated": viewer.authenticated,
            "is_admin": viewer.is_admin,
        },
    }
    if viewer.is_admin:
        admin_token = str(settings_data.get("sub2api_admin_token") or "").strip()
        admin_jwt = str(settings_data.get("sub2api_admin_jwt") or "").strip()
        payload["upstream"] = {
            "provider_base_url": str(settings_data.get("provider_base_url") or ""),
            "auth_base_url": str(settings_data.get("auth_base_url") or ""),
            "effective_provider_base_url": _effective_provider_base_url(settings_data, settings),
            "effective_auth_base_url": _effective_auth_base_url(settings_data, settings),
            "recharge_url": str(settings_data.get("recharge_url") or ""),
            "effective_recharge_url": _effective_recharge_url(settings_data, settings),
            "sub2api_admin_token_set": bool(admin_token or settings.sub2api_admin_token),
            "sub2api_admin_token_hint": _mask_key(admin_token or settings.sub2api_admin_token),
            "sub2api_admin_jwt_set": bool(admin_jwt or settings.sub2api_admin_jwt),
            "sub2api_admin_jwt_hint": _mask_key(admin_jwt or settings.sub2api_admin_jwt),
        }
    return payload


def _normalize_upstream_url(value: Any, label: str = "Upstream URL") -> str:
    text = str(value or "").strip().rstrip("/")
    if not text:
        return ""
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=400, detail=f"{label} must be a valid http:// or https:// URL")
    return text


def _effective_provider_base_url(settings_data: dict[str, Any], settings: Settings) -> str:
    return str(settings_data.get("provider_base_url") or settings.provider_base_url).strip().rstrip("/")


def _effective_auth_base_url(settings_data: dict[str, Any], settings: Settings) -> str:
    return str(settings_data.get("auth_base_url") or settings.auth_base_url).strip().rstrip("/")


def _effective_recharge_url(settings_data: dict[str, Any], settings: Settings) -> str:
    return str(settings_data.get("recharge_url") or settings.recharge_url).strip().rstrip("/")


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
    *,
    grant_trial: bool = False,
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
    api_key = await _resolve_auth_api_key(
        db,
        settings,
        auth_client,
        auth_base_url,
        access_token,
        owner_id=owner_id,
        sub2api_user_id=user_id,
        email=str(user.get("email") or ""),
        display_name=display_name,
        grant_trial=grant_trial,
    )

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


async def _resolve_auth_api_key(
    db: Database,
    settings: Settings,
    auth_client: Sub2APIAuthClient,
    auth_base_url: str,
    access_token: str,
    *,
    owner_id: str,
    sub2api_user_id: int,
    email: str,
    display_name: str,
    grant_trial: bool,
) -> str:
    api_key: str | None = None
    if grant_trial and settings.trial_key_enabled:
        api_key = await _resolve_trial_api_key(
            db,
            settings,
            auth_client,
            auth_base_url,
            access_token,
            owner_id=owner_id,
            sub2api_user_id=sub2api_user_id,
            email=email,
            display_name=display_name,
        )
    if not api_key:
        api_key = await _resolve_user_api_key(auth_client, auth_base_url, access_token)
    if not grant_trial:
        await _retry_partial_trial_balance(
            db,
            settings,
            auth_client,
            auth_base_url,
            owner_id=owner_id,
            sub2api_user_id=sub2api_user_id,
            email=email,
        )
    return api_key


async def _resolve_trial_api_key(
    db: Database,
    settings: Settings,
    auth_client: Sub2APIAuthClient,
    auth_base_url: str,
    access_token: str,
    *,
    owner_id: str,
    sub2api_user_id: int,
    email: str,
    display_name: str,
) -> str | None:
    existing_grant = db.get_trial_grant(owner_id=owner_id) or db.get_trial_grant(sub2api_user_id=sub2api_user_id)
    if existing_grant and str(existing_grant.get("status") or "") != "failed":
        current = db.get_config(owner_id, settings, user_name=display_name)
        managed_key = str(current.get("managed_api_key") or "").strip()
        if managed_key:
            return managed_key

    try:
        keys = await auth_client.list_keys(auth_base_url, access_token)
        selected = _select_trial_key(keys, settings.trial_key_name_prefix)
        created = selected or await _create_trial_api_key(settings, auth_client, auth_base_url, access_token)
        key = str(created.get("key") or "").strip()
        if not key:
            raise ProviderError(502, "JokoAI did not return a usable trial API key", created)

        balance_granted, balance_error = await _grant_trial_balance(
            settings,
            auth_client,
            auth_base_url,
            sub2api_user_id,
            db.get_site_settings(),
        )
        status = "created" if not balance_error else "partial"
        if selected and not balance_granted and not balance_error:
            status = "existing"
        db.mark_trial_grant(
            owner_id=owner_id,
            sub2api_user_id=sub2api_user_id,
            email=email,
            key_id=str(created.get("id") or ""),
            key_hint=_mask_key(key),
            quota_usd=settings.trial_key_quota_usd,
            balance_granted_usd=balance_granted,
            status=status,
            error=balance_error,
        )
        return key
    except ProviderError as exc:
        db.mark_trial_grant(
            owner_id=owner_id,
            sub2api_user_id=sub2api_user_id,
            email=email,
            quota_usd=settings.trial_key_quota_usd,
            status="failed",
            error=exc.message,
        )
        return None


async def _create_trial_api_key(
    settings: Settings,
    auth_client: Sub2APIAuthClient,
    auth_base_url: str,
    access_token: str,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": f"{settings.trial_key_name_prefix}-{utc_now()[:10]}",
        "quota": settings.trial_key_quota_usd,
    }
    if settings.trial_key_expires_days > 0:
        payload["expires_in_days"] = settings.trial_key_expires_days
    group_id = await _resolve_default_key_group_id(auth_client, auth_base_url, access_token)
    if group_id is not None:
        payload["group_id"] = group_id
    return await auth_client.create_key(auth_base_url, access_token, payload)


async def _retry_partial_trial_balance(
    db: Database,
    settings: Settings,
    auth_client: Sub2APIAuthClient,
    auth_base_url: str,
    *,
    owner_id: str,
    sub2api_user_id: int,
    email: str,
) -> None:
    grant = db.get_trial_grant(owner_id=owner_id) or db.get_trial_grant(sub2api_user_id=sub2api_user_id)
    if not grant or str(grant.get("status") or "") != "partial":
        return
    if float(grant.get("balance_granted_usd") or 0) > 0:
        return
    balance_granted, balance_error = await _grant_trial_balance(
        settings,
        auth_client,
        auth_base_url,
        sub2api_user_id,
        db.get_site_settings(),
    )
    if not balance_granted and not balance_error:
        return
    db.mark_trial_grant(
        owner_id=owner_id,
        sub2api_user_id=sub2api_user_id,
        email=email or str(grant.get("email") or ""),
        key_id=str(grant.get("key_id") or ""),
        key_hint=str(grant.get("key_hint") or ""),
        quota_usd=float(grant.get("quota_usd") or 0),
        balance_granted_usd=balance_granted,
        status="created" if balance_granted and not balance_error else "partial",
        error=balance_error,
    )


async def _grant_trial_balance(
    settings: Settings,
    auth_client: Sub2APIAuthClient,
    auth_base_url: str,
    sub2api_user_id: int,
    site_settings: dict[str, Any] | None = None,
) -> tuple[float, str | None]:
    if not settings.trial_balance_grant_enabled or settings.trial_balance_usd <= 0:
        return 0, None
    configured_admin_token = str((site_settings or {}).get("sub2api_admin_token") or settings.sub2api_admin_token).strip()
    configured_admin_jwt = str((site_settings or {}).get("sub2api_admin_jwt") or settings.sub2api_admin_jwt).strip()
    token = configured_admin_token or configured_admin_jwt
    token_type = "api_key" if configured_admin_token else "jwt"
    if not token:
        return 0, "未配置 SUB2API_ADMIN_TOKEN 或 SUB2API_ADMIN_JWT，已创建试用 Key 但未自动赠送余额"
    payload = {
        "balance": settings.trial_balance_usd,
        "operation": "add",
        "notes": "joko-image2 new user trial grant",
    }
    try:
        await auth_client.admin_update_user_balance(
            auth_base_url,
            token,
            sub2api_user_id,
            payload,
            token_type=token_type,
        )
    except ProviderError as exc:
        return 0, f"试用余额赠送失败：{exc.message}"
    return settings.trial_balance_usd, None


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
    group_id = await _resolve_default_key_group_id(auth_client, auth_base_url, access_token)
    if group_id is not None:
        payload["group_id"] = group_id
    created = await auth_client.create_key(auth_base_url, access_token, payload)
    key = str(created.get("key") or "").strip()
    if not key:
        raise HTTPException(status_code=502, detail="JokoAI did not return a usable API key")
    return key


def _select_trial_key(keys: list[dict[str, Any]], name_prefix: str) -> dict[str, Any] | None:
    prefix = name_prefix.strip().lower()
    if not prefix:
        return None
    candidates = [
        item
        for item in keys
        if isinstance(item.get("key"), str)
        and item.get("key")
        and str(item.get("name") or "").lower().startswith(prefix)
    ]
    if not candidates:
        return None
    return _select_existing_key(candidates)


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


async def _resolve_default_key_group_id(
    auth_client: Sub2APIAuthClient,
    auth_base_url: str,
    access_token: str,
) -> int | None:
    try:
        groups = await auth_client.list_available_groups(auth_base_url, access_token)
    except Exception:
        return None
    selected = _select_default_key_group(groups)
    if selected is None:
        return None
    group_id = selected.get("id")
    try:
        return int(group_id)
    except (TypeError, ValueError):
        return None


def _select_default_key_group(groups: list[dict[str, Any]]) -> dict[str, Any] | None:
    active_groups = [group for group in groups if str(group.get("status") or "active").lower() == "active"]
    candidates = active_groups or groups
    if not candidates:
        return None

    def text(group: dict[str, Any]) -> str:
        values = [
            group.get("name"),
            group.get("platform"),
            group.get("description"),
            group.get("subscription_type"),
        ]
        return " ".join(str(value or "").lower() for value in values)

    for keyword in ("codex_plus", "codex-plus", "codex plus"):
        for group in candidates:
            if keyword in text(group):
                return group
    for keyword in ("team", "团队"):
        for group in candidates:
            if keyword in text(group):
                return group
    for group in candidates:
        if str(group.get("platform") or "").lower() == "openai":
            return group
    return candidates[0]


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


def _inspiration_ai_search_payload(request: InspirationAISearchRequest, settings: Settings) -> dict[str, Any]:
    return {
        "model": (request.model or settings.prompt_optimizer_model).strip(),
        "messages": [
            {"role": "system", "content": INSPIRATION_AI_SEARCH_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "请把下面的自然语言需求提炼为案例库搜索关键词。\n\n"
                    f"用户需求：{request.query.strip()}"
                ),
            },
        ],
        "temperature": 0.2,
        "max_tokens": 500,
        "stream": False,
    }


def _ecommerce_publish_copy_payload(request: EcommercePublishCopyRequest, settings: Settings) -> dict[str, Any]:
    context = {
        "product_name": request.product_name.strip(),
        "materials": request.materials.strip(),
        "selling_points": request.selling_points.strip(),
        "scenarios": request.scenarios.strip(),
        "platform": request.platform.strip(),
        "style": request.style.strip(),
        "extra_requirements": request.extra_requirements.strip(),
        "image_count": request.image_count,
        "size": request.size or "",
        "aspect_ratio": request.aspect_ratio or "",
    }
    return {
        "model": (request.model or settings.prompt_optimizer_model).strip(),
        "messages": [
            {"role": "system", "content": ECOMMERCE_PUBLISH_COPY_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "请基于以下电商详情页项目参数，生成一个可直接发布的小红书/社媒标题和正文。\n\n"
                    f"{json.dumps(context, ensure_ascii=False)}"
                ),
            },
        ],
        "temperature": 0.75,
        "max_tokens": 1200,
        "stream": False,
    }


def _series_prompt_planner_payload(
    *,
    prompt: str,
    mode: str,
    image_count: int,
    model: str,
    size: str,
    aspect_ratio: str,
    quality: str,
    settings: Settings,
) -> dict[str, Any]:
    context = {
        "mode": mode,
        "image_count": image_count,
        "model": model,
        "size": size,
        "aspect_ratio": aspect_ratio,
        "quality": quality,
        "user_prompt": prompt.strip(),
    }
    return {
        "model": settings.prompt_optimizer_model.strip(),
        "messages": [
            {"role": "system", "content": SERIES_PROMPT_PLANNER_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "请把以下总需求拆解成系列图像提示词。"
                    "每张图必须承担不同内容模块，但整体像同一套详情页/海报系列。\n\n"
                    f"{json.dumps(context, ensure_ascii=False)}"
                ),
            },
        ],
        "temperature": 0.35,
        "max_tokens": 3800,
        "stream": False,
    }


def _ecommerce_prompt_from_fields(
    *,
    product_name: str,
    materials: str,
    selling_points: str,
    scenarios: str,
    platform: str,
    style: str,
    extra_requirements: str,
    image_count: int,
) -> str:
    parts = [
        "根据上传的商品图片生成电商产品详情页系列图。",
        f"商品名称：{product_name.strip() or '未填写'}",
        f"材质/用料：{materials.strip() or '未填写'}",
        f"核心卖点：{selling_points.strip() or '未填写'}",
        f"使用场景：{scenarios.strip() or '未填写'}",
        f"目标平台：{platform.strip() or '通用电商'}",
        f"视觉风格：{style.strip() or '高级、干净、统一'}",
        f"图片张数：{image_count} 张，每张作为详情页中的一个连续模块。",
        "要求每一屏都有清晰标题和说明文案，字体样式、排版网格、色调和产品呈现方式保持一致。",
        "每一屏内容不能重复，应分别覆盖主卖点、使用场景、材质细节、成分/结构、尺寸定制、百搭优势、细节工艺、信任背书或转化总结。",
    ]
    if extra_requirements.strip():
        parts.append(f"额外要求：{extra_requirements.strip()}")
    return "\n".join(parts)


def _ecommerce_product_analyzer_payload(
    *,
    upload: dict[str, Any],
    uploads: list[dict[str, Any]] | None = None,
    prompt: str,
    settings: Settings,
) -> dict[str, Any]:
    reference_uploads = uploads or [upload]
    content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                "请综合识别这些商品参考图，并结合以下电商详情页需求输出结构化商品分析。\n"
                "多张图代表同一个商品的不同角度或细节，必须合并为完整商品身份，不要只看第一张。\n\n"
                f"{_reference_notes_text(reference_uploads)}\n\n"
                f"{prompt}"
            ),
        }
    ]
    for reference in reference_uploads:
        path = Path(str(reference["path"]))
        content_type = str(reference.get("content_type") or "image/png")
        data_url = f"data:{content_type};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"
        content.append({"type": "image_url", "image_url": {"url": data_url}})
    return {
        "model": settings.prompt_optimizer_model.strip(),
        "messages": [
            {"role": "system", "content": ECOMMERCE_PRODUCT_ANALYZER_SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ],
        "temperature": 0.2,
        "max_tokens": 1200,
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


async def _plan_series_prompts(
    provider: OpenAICompatibleImageClient,
    config: dict[str, Any],
    settings: Settings,
    *,
    mode: str,
    prompt: str,
    image_count: int,
    model: str,
    size: str,
    aspect_ratio: str,
    quality: str,
) -> dict[str, Any]:
    try:
        provider_response = await provider.chat_completion(
            config,
            _series_prompt_planner_payload(
                prompt=prompt,
                mode=mode,
                image_count=image_count,
                model=model,
                size=size,
                aspect_ratio=aspect_ratio,
                quality=quality,
                settings=settings,
            ),
        )
        text = _extract_chat_completion_text(provider_response)
        plan = _parse_series_prompt_plan(text, image_count)
        if plan is not None:
            plan["source"] = "planner"
            return plan
    except Exception:
        pass
    plan = _fallback_series_prompt_plan(
        prompt=prompt,
        mode=mode,
        image_count=image_count,
        size=size,
        aspect_ratio=aspect_ratio,
        quality=quality,
    )
    plan["source"] = "fallback"
    return plan


async def _analyze_ecommerce_product(
    provider: OpenAICompatibleImageClient,
    config: dict[str, Any],
    settings: Settings,
    *,
    upload: dict[str, Any],
    uploads: list[dict[str, Any]] | None = None,
    prompt: str,
) -> dict[str, Any]:
    try:
        provider_response = await provider.chat_completion(
            config,
            _ecommerce_product_analyzer_payload(upload=upload, uploads=uploads, prompt=prompt, settings=settings),
        )
        parsed = _extract_json_object(_extract_chat_completion_text(provider_response))
        if isinstance(parsed, dict):
            parsed["source"] = "vision"
            return parsed
    except Exception:
        pass
    return {
        "source": "fallback",
        "product_type": "",
        "appearance": "根据上传参考图保持商品主体、轮廓、颜色、材质和结构一致。",
        "visible_material": "",
        "colors": [],
        "shape": "",
        "details": [],
        "generation_constraints": "严格参考上传商品图，保持同一商品主体、颜色、材质、比例、结构和轮廓一致。",
    }


async def _parse_history_edit_request(
    raw_request: Request,
    *,
    prompt: str | None,
    model: str | None,
    size: str | None,
    aspect_ratio: str | None,
    quality: str | None,
    reference_notes: str | None = None,
) -> HistoryEditRequest:
    content_type = raw_request.headers.get("content-type", "").lower()
    if "application/json" in content_type:
        try:
            payload = await raw_request.json()
            return HistoryEditRequest.model_validate(payload)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.errors()) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid JSON body") from exc

    try:
        return HistoryEditRequest.model_validate(
            {
                "prompt": prompt,
                "model": model,
                "size": size,
                "aspect_ratio": aspect_ratio,
                "quality": quality,
                "reference_notes": _parse_reference_notes(reference_notes),
            }
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc


REFERENCE_ROLE_DEFAULTS = {
    0: "主体/主图",
    1: "参考图 2",
    2: "参考图 3",
    3: "参考图 4",
    4: "参考图 5",
    5: "参考图 6",
}


def _parse_reference_notes(value: str | None) -> list[dict[str, Any]]:
    if not value:
        return []
    try:
        payload = json.loads(value)
    except (TypeError, ValueError):
        return []
    if not isinstance(payload, list):
        return []
    notes: list[dict[str, Any]] = []
    for index, item in enumerate(payload[:12]):
        if not isinstance(item, dict):
            continue
        raw_index = item.get("index")
        try:
            note_index = int(raw_index)
        except (TypeError, ValueError):
            note_index = index
        notes.append(
            {
                "index": max(0, note_index),
                "role": str(item.get("role") or "").strip()[:80],
                "note": str(item.get("note") or "").strip()[:600],
                "primary": bool(item.get("primary")),
                "explicit": True,
            }
        )
    return notes


def _normalize_reference_notes(notes: list[dict[str, Any]] | None, count: int) -> list[dict[str, Any]]:
    by_index: dict[int, dict[str, Any]] = {}
    for item in notes or []:
        try:
            index = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        if 0 <= index < count:
            by_index[index] = item
    normalized: list[dict[str, Any]] = []
    for index in range(count):
        item = by_index.get(index) or {}
        role = str(item.get("role") or "").strip()
        note = str(item.get("note") or "").strip()
        normalized.append(
            {
                "index": index,
                "role": role or REFERENCE_ROLE_DEFAULTS.get(index, f"参考图 {index + 1}"),
                "note": note,
                "primary": bool(item.get("primary")) or index == 0,
                "explicit": index in by_index,
            }
        )
    return normalized


def _attach_reference_notes(
    uploads: list[dict[str, str]],
    notes: list[dict[str, Any]],
    *,
    start_index: int = 0,
) -> list[dict[str, Any]]:
    attached: list[dict[str, Any]] = []
    for offset, upload in enumerate(uploads):
        index = start_index + offset
        note = notes[offset] if offset < len(notes) else {}
        attached.append(
            {
                **upload,
                "reference_index": index,
                "reference_role": str(note.get("role") or REFERENCE_ROLE_DEFAULTS.get(index, f"参考图 {index + 1}")),
                "reference_note": str(note.get("note") or ""),
                "reference_primary": bool(note.get("primary")) or index == 0,
                "reference_explicit": bool(note.get("explicit")),
            }
        )
    return attached


def _task_reference_notes(uploads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, upload in enumerate(uploads):
        result.append(
            {
                "index": int(upload.get("reference_index") if upload.get("reference_index") is not None else index),
                "role": str(upload.get("reference_role") or REFERENCE_ROLE_DEFAULTS.get(index, f"参考图 {index + 1}")),
                "note": str(upload.get("reference_note") or ""),
                "url": upload.get("url") or "",
                "primary": bool(upload.get("reference_primary")) or index == 0,
                "explicit": bool(upload.get("reference_explicit")) or bool(upload.get("reference_note")),
            }
        )
    return result


def _append_reference_notes_to_prompt(prompt: str, uploads: list[dict[str, Any]]) -> str:
    text = _reference_notes_text(uploads)
    if not text:
        return prompt
    return f"{prompt.strip()}\n\n{text}"


def _reference_notes_text(uploads: list[dict[str, Any]]) -> str:
    notes = [note for note in _task_reference_notes(uploads) if note.get("explicit")]
    if not notes:
        return ""
    lines = [
        "参考图说明：",
        "请严格按每张参考图的用途理解。多张商品角度图共同定义同一个商品身份，正面、侧面、背面和细节都要纳入结构、轮廓、比例、包装信息和材质判断，不要只参考第一张。",
    ]
    for note in notes:
        role = str(note.get("role") or f"参考图 {int(note.get('index') or 0) + 1}").strip()
        content = str(note.get("note") or "").strip()
        suffix = f"，{content}" if content else ""
        lines.append(f"图{int(note.get('index') or 0) + 1}：{role}{suffix}。")
    return "\n".join(lines)


def _history_edit_provider_prompt(prompt: str, *, has_product_reference: bool, extra_references: list[dict[str, Any]]) -> str:
    rules = [
        "单图修改参考图规则：",
        "当前生成任务会基于已有成品图继续修改，必须保留原图的主体构图、商品/角色身份和视觉连续性。",
    ]
    if has_product_reference:
        rules.extend(
            [
                "第一张图是原商品主图，是商品身份参考，优先级最高。",
                "第二张图是当前成品图，是版式、文案层级、画面风格和待修改内容参考。",
                "必须严格保持第一张商品图中的商品主体一致，包括颜色、材质、纹理、结构、比例、轮廓、核心细节和整体形态。",
                "只允许按用户修改要求调整详情页背景、排版、标题、说明文案、辅助装饰、场景氛围或局部细节。",
                "不得重新设计商品，不得改变商品颜色或材质，不得把商品替换成同类其他款式。",
            ]
        )
    else:
        rules.append("第一张图是当前成品图，请以这张图为主要参考继续修改，不要无关重绘。")
    if extra_references:
        rules.append(f"额外上传的 {len(extra_references)} 张参考图只作为指定用途补充，不得覆盖主商品/主成品身份。")
    return _append_reference_notes_to_prompt(f"{prompt.strip()}\n\n" + "\n".join(rules), extra_references)


def _append_ecommerce_consistency_lock(prompt: str, ecommerce_analysis: dict[str, Any] | None) -> str:
    constraints = ""
    if isinstance(ecommerce_analysis, dict):
        raw_constraints = ecommerce_analysis.get("generation_constraints")
        if isinstance(raw_constraints, str):
            constraints = raw_constraints.strip()
    rules = [
        "商品一致性强约束：",
        "必须严格保持上传商品主图中的商品主体一致，包括颜色、材质、纹理、结构、比例、轮廓、核心细节和整体形态。",
        "只允许改变详情页背景、排版、标题、说明文案、辅助装饰和使用场景。",
        "不得重新设计商品，不得改变商品颜色，不得改变商品材质，不得把商品替换成同类其他款式。",
        "如果商品图识别结果和用户字段冲突，以上传商品图可见外观为准。",
    ]
    if constraints:
        rules.append(f"商品识别约束：{constraints}")
    return f"{prompt.strip()}\n\n" + "\n".join(rules)


def _parse_series_prompt_plan(text: str, image_count: int) -> dict[str, Any] | None:
    payload = _extract_json_object(text)
    if not isinstance(payload, dict):
        return None
    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        return None
    items: list[dict[str, Any]] = []
    for index, item in enumerate(raw_items[:image_count], start=1):
        if not isinstance(item, dict):
            continue
        item_prompt = str(item.get("prompt") or "").strip()
        if not item_prompt:
            continue
        items.append(
            {
                "index": index,
                "title": str(item.get("title") or f"第 {index} 屏").strip(),
                "copy": str(item.get("copy") or "").strip(),
                "prompt": item_prompt,
            }
        )
    if len(items) != image_count:
        return None
    return {
        "style_guide": str(payload.get("style_guide") or "").strip(),
        "items": items,
    }


def _extract_json_object(text: str) -> Any | None:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = [line for line in cleaned.splitlines() if not line.strip().startswith("```")]
        cleaned = "\n".join(lines).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        cleaned = cleaned[start : end + 1]
    try:
        return json.loads(cleaned)
    except (TypeError, ValueError):
        return None


def _extract_inspiration_search_query(text: str, fallback_query: str) -> str:
    parsed = _extract_json_object(text)
    if isinstance(parsed, dict):
        query = str(parsed.get("query") or "").strip()
        if query:
            return _fallback_inspiration_search_query(query)
        keywords = parsed.get("keywords")
        if isinstance(keywords, list):
            values = [str(keyword).strip() for keyword in keywords if str(keyword).strip()]
            if values:
                return _fallback_inspiration_search_query(" ".join(values))
    return _fallback_inspiration_search_query(text or fallback_query)


def _fallback_inspiration_search_query(query: str) -> str:
    text = " ".join(str(query or "").replace("\n", " ").split())
    if len(text) <= 120:
        return text
    return text[:120].rsplit(" ", 1)[0] or text[:120]


def _fallback_series_prompt_plan(
    *,
    prompt: str,
    mode: str,
    image_count: int,
    size: str,
    aspect_ratio: str,
    quality: str,
) -> dict[str, Any]:
    modules = [
        ("核心卖点", "突出产品核心利益点、主视觉和购买理由。"),
        ("使用场景", "展示产品在真实生活、电商或目标场景中的使用方式。"),
        ("材质工艺", "解释材质、触感、结构、工艺和品质细节。"),
        ("成分结构", "拆解填充、面料、内部结构或关键参数。"),
        ("尺寸定制", "说明尺寸、规格、定制能力和适配范围。"),
        ("百搭优势", "展示和不同环境、风格、用途的搭配优势。"),
        ("细节特写", "用近景突出纹理、边缘、缝线、质感和细节。"),
        ("信任背书", "强调品质保障、耐用性、售后或适合人群。"),
        ("收尾转化", "做详情页结尾总结，强化品牌感和购买行动。"),
    ]
    style_guide = (
        f"统一系列视觉：{aspect_ratio} 竖版/横版构图按参数执行，{size}，{quality} quality；"
        "同一产品主体、同一色调、同一字体样式、同一标题和正文排版网格、同一电商详情页视觉系统；"
        "标题简洁可读，正文短句清晰，避免乱码和不一致的品牌符号。"
    )
    if mode == "edit":
        style_guide += " 严格参考上传图片中的产品主体、外观、材质和结构，保持产品一致性。"
    items = []
    for index in range(1, image_count + 1):
        title, copy = modules[index - 1] if index <= len(modules) else (f"第 {index} 屏", "补充一个不重复的产品详情模块。")
        prompt_parts = [
            f"{prompt.strip()}",
            f"这是同一套系列详情页的第 {index}/{image_count} 屏，主题标题：{title}。",
            f"本屏说明文案：{copy}",
            style_guide,
            "本屏必须和其他屏保持统一色调、字体、标题位置、正文排版、产品比例和视觉语言，但内容模块不能重复。",
        ]
        if mode == "edit":
            prompt_parts.append("根据上传参考图中的同一个产品生成本屏，产品外观必须一致。")
        items.append({"index": index, "title": title, "copy": copy, "prompt": "\n".join(prompt_parts)})
    return {"style_guide": style_guide, "items": items}


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
        if not isinstance(request_payload, dict):
            raise ValueError(f"{task['mode'].title()} task payload was missing")

        if task["mode"] == "generate":
            requested_count = _request_image_count(request_payload)
            if requested_count > 1:
                await _run_series_image_task(
                    db,
                    settings,
                    provider,
                    auth_client,
                    task_id=task_id,
                    task=task,
                    config=config,
                    request_payload=request_payload,
                    image_count=requested_count,
                )
                return
            provider_response = await _call_provider_with_retries(
                lambda: provider.generate_image(config, _single_image_payload(request_payload))
            )
        elif task["mode"] == "edit":
            fields = request_payload.get("fields")
            uploads = request_payload.get("uploads")
            if not isinstance(fields, dict) or not isinstance(uploads, list):
                raise ValueError("Edit task payload was incomplete")
            image_files = [_load_saved_upload(item) for item in uploads]
            if not image_files:
                raise ValueError("Edit task is missing source images")
            saved_mask = request_payload.get("mask")
            mask_file = _load_saved_upload(saved_mask) if isinstance(saved_mask, dict) else None
            requested_count = _request_image_count(fields)
            if requested_count > 1:
                await _run_series_image_task(
                    db,
                    settings,
                    provider,
                    auth_client,
                    task_id=task_id,
                    task=task,
                    config=config,
                    request_payload=request_payload,
                    image_count=requested_count,
                    image_files=image_files,
                    mask_file=mask_file,
                )
                return
            provider_response = await _call_provider_with_retries(
                lambda: provider.edit_image(config, _single_image_payload(fields), image_files, mask_file)
            )
        else:
            raise ValueError(f"Unsupported task mode: {task['mode']}")

        latest_task = db.get_image_task_by_id(task_id) or task
        replace_history_id = _replace_history_id_for_task(request_payload, latest_task)
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
            task_id=task_id,
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
            batch_index=0,
            replace_history_id=replace_history_id,
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
            task_id=task_id,
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
            task_id=task_id,
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


async def _run_series_image_task(
    db: Database,
    settings: Settings,
    provider: OpenAICompatibleImageClient,
    auth_client: Sub2APIAuthClient,
    *,
    task_id: str,
    task: dict[str, Any],
    config: dict[str, Any],
    request_payload: dict[str, Any],
    image_count: int,
    image_files: list[tuple[str, bytes, str]] | None = None,
    mask_file: tuple[str, bytes, str] | None = None,
) -> None:
    history_ids: list[str] = []
    usage_items: list[Any] = []
    created_values: list[Any] = []
    partial_errors: list[dict[str, Any]] = []
    latest_for_plan = db.get_image_task_by_id(task_id) or task
    ecommerce_analysis = None
    ecommerce_context = request_payload.get("ecommerce")
    if isinstance(ecommerce_context, dict):
        ecommerce_analysis = ecommerce_context.get("analysis")
    reference_note_prompt = _reference_notes_text(request_payload.get("uploads") or [])
    planning_prompt = latest_for_plan["prompt"]
    if isinstance(ecommerce_analysis, dict):
        planning_prompt = (
            f"{planning_prompt}\n\n"
            "商品图识别结果：\n"
            f"{json.dumps(ecommerce_analysis, ensure_ascii=False)}"
        )
    if reference_note_prompt:
        planning_prompt = f"{planning_prompt}\n\n{reference_note_prompt}"
    plan = await _plan_series_prompts(
        provider,
        config,
        settings,
        mode=latest_for_plan["mode"],
        prompt=planning_prompt,
        image_count=image_count,
        model=latest_for_plan["model"],
        size=latest_for_plan["size"],
        aspect_ratio=latest_for_plan.get("aspect_ratio") or "",
        quality=latest_for_plan["quality"],
    )
    plan_items = plan.get("items") if isinstance(plan, dict) else []
    if not isinstance(plan_items, list) or len(plan_items) != image_count:
        plan = _fallback_series_prompt_plan(
            prompt=planning_prompt,
            mode=latest_for_plan["mode"],
            image_count=image_count,
            size=latest_for_plan["size"],
            aspect_ratio=latest_for_plan.get("aspect_ratio") or "",
            quality=latest_for_plan["quality"],
        )
        plan["source"] = "fallback"
        plan_items = plan["items"]

    db.update_image_task(
        task_id,
        {
            "result": {
                "count_requested": image_count,
                "count_succeeded": 0,
                "ecommerce_analysis": ecommerce_analysis,
                "series_plan": _public_series_plan(plan),
                "usage": [],
                "partial_errors": [],
            },
        },
    )

    for index in range(image_count):
        latest_task = db.get_image_task_by_id(task_id) or task
        plan_item = plan_items[index] if index < len(plan_items) and isinstance(plan_items[index], dict) else {}
        item_prompt = str(plan_item.get("prompt") or latest_task["prompt"]).strip()
        provider_item_prompt = (
            _append_ecommerce_consistency_lock(item_prompt, ecommerce_analysis)
            if isinstance(ecommerce_analysis, dict)
            else item_prompt
        )
        if reference_note_prompt:
            provider_item_prompt = f"{provider_item_prompt}\n\n{reference_note_prompt}"
        try:
            if latest_task["mode"] == "edit":
                fields = request_payload.get("fields")
                if not isinstance(fields, dict) or image_files is None:
                    raise ValueError("Edit task payload was incomplete")
                item_payload = _single_image_payload(fields)
                item_payload["prompt"] = provider_item_prompt
                provider_response = await _call_provider_with_retries(
                    lambda: provider.edit_image(config, item_payload, image_files, mask_file)
                )
            else:
                item_payload = _single_image_payload(request_payload)
                item_payload["prompt"] = provider_item_prompt
                provider_response = await _call_provider_with_retries(
                    lambda: provider.generate_image(config, item_payload)
                )
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
                task_id=task_id,
                mode=latest_task["mode"],
                prompt=item_prompt,
                model=latest_task["model"],
                size=latest_task["size"],
                aspect_ratio=latest_task.get("aspect_ratio") or "",
                quality=latest_task["quality"],
                provider_response=provider_response,
                ledger_cost=ledger_cost,
                input_image_url=latest_task.get("input_image_url"),
                input_image_path=latest_task.get("input_image_path"),
                batch_index=len(history_ids),
            )
            history_ids.extend(item["id"] for item in items)
            usage_items.append(provider_response.get("usage"))
            created_values.append(provider_response.get("created"))
            db.update_image_task(
                task_id,
                {
                    "result_history_ids": history_ids,
                    "result": {
                        "count_requested": image_count,
                        "count_succeeded": len(history_ids),
                        "ecommerce_analysis": ecommerce_analysis,
                        "series_plan": _public_series_plan(plan),
                        "usage": usage_items,
                        "partial_errors": partial_errors,
                    },
                },
            )
        except ProviderError as exc:
            partial_errors.append({"index": index, "error": exc.message, "provider_response": exc.payload})
        except Exception as exc:
            partial_errors.append({"index": index, "error": str(exc), "provider_response": None})

    completed_at = utc_now()
    if history_ids:
        db.update_image_task(
            task_id,
            {
                "status": "succeeded",
                "completed_at": completed_at,
                "result_history_ids": history_ids,
                "result": {
                    "created": created_values[-1] if created_values else None,
                    "created_values": created_values,
                    "usage": usage_items,
                    "count_requested": image_count,
                    "count_succeeded": len(history_ids),
                    "ecommerce_analysis": ecommerce_analysis,
                    "series_plan": _public_series_plan(plan),
                    "partial_errors": partial_errors,
                },
                "error": None if not partial_errors else f"{len(partial_errors)} image(s) failed in this batch",
            },
        )
        return

    message = partial_errors[0]["error"] if partial_errors else "Image batch generation failed"
    latest_task = db.get_image_task_by_id(task_id) or task
    failed = _record_failed_history(
        db,
        owner_id=latest_task["owner_id"],
        task_id=task_id,
        mode=latest_task["mode"],
        prompt=latest_task["prompt"],
        model=latest_task["model"],
        size=latest_task["size"],
        aspect_ratio=latest_task.get("aspect_ratio") or "",
        quality=latest_task["quality"],
        message=message,
        provider_response=partial_errors[0].get("provider_response") if partial_errors else None,
        input_image_url=latest_task.get("input_image_url"),
        input_image_path=latest_task.get("input_image_path"),
    )
    db.update_image_task(
        task_id,
        {
            "status": "failed",
            "completed_at": completed_at,
            "result_history_ids": [failed["id"]] if failed else [],
            "result": {
                "error": message,
                "usage": None,
                "count_requested": image_count,
                "count_succeeded": 0,
                "ecommerce_analysis": ecommerce_analysis,
                "series_plan": _public_series_plan(plan),
                "partial_errors": partial_errors,
            },
            "error": message,
        },
    )


def _public_series_plan(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": plan.get("source") or "",
        "style_guide": plan.get("style_guide") or "",
        "items": [
            {
                "index": item.get("index"),
                "title": item.get("title") or "",
                "copy": item.get("copy") or "",
                "prompt": item.get("prompt") or "",
            }
            for item in plan.get("items", [])
            if isinstance(item, dict)
        ],
    }


def _request_image_count(payload: dict[str, Any]) -> int:
    try:
        return max(1, min(9, int(payload.get("n") or 1)))
    except (TypeError, ValueError):
        return 1


def _single_image_payload(payload: dict[str, Any]) -> dict[str, Any]:
    single = dict(payload)
    single["n"] = 1
    return single


def _replace_history_id_for_task(request_payload: dict[str, Any], task: dict[str, Any]) -> str | None:
    if task.get("mode") != "edit":
        return None
    if _request_image_count(request_payload.get("fields") if isinstance(request_payload.get("fields"), dict) else {}) != 1:
        return None
    ecommerce = request_payload.get("ecommerce")
    replace_history_id = str(request_payload.get("replace_history_id") or "").strip()
    source_history_id = str(request_payload.get("source_history_id") or "").strip()
    if isinstance(ecommerce, dict) and replace_history_id and replace_history_id == source_history_id:
        return replace_history_id
    return None


async def _persist_image_response(
    db: Database,
    settings: Settings,
    *,
    owner_id: str,
    task_id: str | None = None,
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
    batch_index: int = 0,
    replace_history_id: str | None = None,
) -> list[dict[str, Any]]:
    data = provider_response.get("data")
    if not isinstance(data, list) or not data:
        raise ValueError("Provider response did not contain image data")

    records = []
    for item in data:
        if not isinstance(item, dict):
            continue
        history_id = replace_history_id if replace_history_id and not records else uuid4().hex
        storage_id = f"{history_id}-{uuid4().hex[:8]}" if replace_history_id and not records else history_id
        saved = await save_provider_image(settings, storage_id, item)
        history_payload = {
            "id": history_id,
            "task_id": task_id,
            "batch_index": batch_index + len(records),
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
            "error": None,
        }
        if replace_history_id and not records:
            update_payload = dict(history_payload)
            update_payload.pop("id", None)
            update_payload.pop("task_id", None)
            update_payload.pop("batch_index", None)
            record = db.update_history(owner_id, replace_history_id, update_payload)
            if record is None:
                raise ValueError("History item to replace was not found")
        else:
            record = db.create_history(owner_id, history_payload)
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
    task_id: str | None,
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
            "task_id": task_id,
            "batch_index": 0,
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
