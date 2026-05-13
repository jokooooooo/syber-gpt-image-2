from __future__ import annotations

import asyncio
import base64
import json
import zipfile
from contextlib import asynccontextmanager
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlparse
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError

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


class EcommerceAnalyzeRequest(BaseModel):
    product_name: str = Field(default="", max_length=300)
    materials: str = Field(default="", max_length=1200)
    selling_points: str = Field(default="", max_length=1600)
    scenarios: str = Field(default="", max_length=1200)
    platform: str = Field(default="", max_length=120)
    style: str = Field(default="", max_length=800)
    extra_requirements: str = Field(default="", max_length=1600)
    image_count: int = Field(default=4, ge=1, le=9)
    size: str | None = Field(default=None, max_length=80)
    aspect_ratio: str | None = Field(default=None, max_length=20)
    model: str | None = Field(default=None, max_length=120)


class EcommercePlanScreen(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    title: str = Field(default="", max_length=300)
    body_copy: str = Field(default="", alias="copy", max_length=1200)
    layout_type: str = Field(default="", max_length=80)
    visual_goal: str = Field(default="", max_length=1200)
    copy_density: str = Field(default="", max_length=80)
    needs_model: bool | None = None
    needs_specs: bool | None = None
    needs_closeup: bool | None = None
    reference_focus: list[str] | None = None


class EcommerceSelectedPlan(BaseModel):
    name: str = Field(default="", max_length=300)
    platform: str = Field(default="", max_length=120)
    style: str = Field(default="", max_length=800)
    image_count: int = Field(default=1, ge=1, le=9)
    materials: str = Field(default="", max_length=1200)
    selling_points: str = Field(default="", max_length=1600)
    scenarios: str = Field(default="", max_length=1200)
    extra_requirements: str = Field(default="", max_length=1600)
    reason: str = Field(default="", max_length=1200)
    screens: list[EcommercePlanScreen] = Field(default_factory=list)


class PaymentCreateOrderRequest(BaseModel):
    amount: float = Field(gt=0)
    payment_type: str = Field(min_length=1, max_length=80)
    order_type: str = Field(default="balance", max_length=40)
    plan_id: int | None = Field(default=None, ge=1)


class PaymentVerifyOrderRequest(BaseModel):
    out_trade_no: str = Field(min_length=1, max_length=160)


SIZE_PRESETS: dict[str, dict[str, str]] = {
    "FAST": {
        "1:1": "1024x1024",
        "16:9": "1360x768",
        "9:16": "768x1360",
        "3:2": "1248x832",
        "2:3": "832x1248",
        "4:3": "1184x896",
        "3:4": "896x1184",
    },
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
ALLOWED_PRESET_DIMENSIONS = set(SIZE_TIER_BY_DIMENSION)

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
2. JSON 格式必须是：{"style_guide":"...", "items":[{"index":1,"title":"...","copy":"...","layout_type":"...","visual_goal":"...","prompt":"..."}]}。
3. items 数量必须等于用户要求的图片张数，index 从 1 开始连续。
4. 每个 prompt 都必须可以独立用于 gpt-image-2 生图/改图接口。
5. 每个 prompt 都要包含统一风格约束：同一产品、同一色调、同一字体样式、同一详情页视觉系统。
6. 如果是电商详情页、海报组、主图/副图、故事分镜等需求，要自动拆成不同页面/屏幕/模块，不要重复同一张图。
7. 如果是改图模式，prompt 必须明确要求严格参考上传图片中的主体、材质、结构和外观，只改变本屏需要表达的场景、文案和布局。
8. 不要把每一屏都做成“顶部大标题 + 商品 + 一段文案”的标题卡片。必须按 layout_type 做差异化：模特上身、场景穿搭、参数表、尺码表、细节局部放大、材质微距、多角度拼版、卖点对比、收尾转化等。
9. 画面中文字必须简洁、清晰、可读，避免乱码；并非每张都需要大标题。参数/尺码/细节页可以用表格、标注线、局部放大、信息卡表达。
10. title 是内部模块名，只用于后台列表和规划结构；不要把 title 原样写进图片画面。画面文案应来自 copy、卖点、参数或自然短句。
11. 如果用户上下文包含 selected_plan，说明用户已经选定了固定蓝图；你不能改变屏数和顺序，但必须根据每屏 layout_type/visual_goal 扩写成真实详情页画面，不要机械复述标题。
12. 保持原提示词主要语言；中文输入输出中文，英文输入输出英文。"""

ECOMMERCE_PRODUCT_ANALYZER_SYSTEM_PROMPT = """你是 JokoAI 的电商商品图识别分析师。
用户会上传一张或多张商品参考图，并提供商品名称、材质、卖点、平台和风格。你的任务是综合识别商品外观并输出可用于后续电商详情页生成的结构化信息和推荐设计方案。
要求：
1. 只输出 JSON，不要 Markdown、解释或代码块。
2. JSON 格式必须是：{"product_type":"...","appearance":"...","visible_material":"...","colors":["..."],"shape":"...","details":["..."],"selling_points":["..."],"target_audience":["..."],"use_scenarios":["..."],"style_suggestions":["..."],"generation_constraints":"...","recommended_plans":[{"name":"...","platform":"...","style":"...","image_count":4,"materials":"...","selling_points":"...","scenarios":"...","extra_requirements":"...","reason":"...","screens":[{"title":"...","copy":"...","layout_type":"hero|model_fit|scene_lifestyle|material_closeup|detail_callout|spec_table|size_chart|multi_angle|comparison|social_cover|conversion","visual_goal":"...","copy_density":"low|medium|high","needs_model":false,"needs_specs":false,"needs_closeup":false,"reference_focus":["..."]}]}]}。
3. 如果有正面、侧面、背面、材质细节等多角度参考图，必须把它们合并理解为同一商品的完整外观，不得只依据第一张图。
4. generation_constraints 要明确说明生成时必须保持商品主体、颜色、材质、比例、结构、轮廓一致，并保留多角度参考图中可见的关键侧面/背面/细节信息。
5. recommended_plans 给出 3 个适合普通商家的方案，必须覆盖不同用途，例如电商详情页、小红书种草图、白底主图/场景图/直播带货图。每个方案都要能一键填入生成表单。
6. 每个 recommended_plan 的 image_count 必须等于用户填写字段里的 image_count，screens 数量也必须等于 image_count，方案名不得出现“四屏/三屏/五屏”等和 image_count 不一致的字样。
7. screens 必须从商品品类和用途出发完整规划，不能只给前 4 屏后面留空；生鲜、水果、食品、服装、数码、家居等品类要使用不同模块。
8. 电商详情页方案不能只是标题列表，要像真实详情页脚本：至少混合主视觉、场景/模特、参数/规格、材质/细节、对比/卖点、收尾转化等页面类型。不要每屏都要求顶部大标题。
9. 不确定的信息不要编造，优先根据图片可见信息和用户输入综合判断。
10. 中文输入输出中文，英文输入输出英文。"""

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
    trial_balance_usd: float | None = Field(default=None, ge=0, le=1000)


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
            raise HTTPException(status_code=exc.status_code, detail=_provider_error_message(exc)) from exc

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
            raise HTTPException(status_code=exc.status_code, detail=_provider_error_message(exc)) from exc

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
            raise HTTPException(status_code=exc.status_code, detail=_provider_error_message(exc)) from exc

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
            raise HTTPException(status_code=exc.status_code, detail=_provider_error_message(exc)) from exc

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
            raise HTTPException(status_code=exc.status_code, detail=_provider_error_message(exc)) from exc

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
            raise HTTPException(status_code=exc.status_code, detail=_provider_error_message(exc)) from exc

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
            raise HTTPException(status_code=exc.status_code, detail=_provider_error_message(exc)) from exc

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
            raise HTTPException(status_code=exc.status_code, detail=_provider_error_message(exc)) from exc

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
            raise HTTPException(status_code=exc.status_code, detail=_provider_error_message(exc)) from exc

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
            raise HTTPException(status_code=exc.status_code, detail=_provider_error_message(exc)) from exc

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
            raise HTTPException(status_code=exc.status_code, detail=_provider_error_message(exc)) from exc

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
            raise HTTPException(status_code=exc.status_code, detail=_provider_error_message(exc)) from exc

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
            raise HTTPException(status_code=exc.status_code, detail=_provider_error_message(exc)) from exc
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

    @app.get("/api/tasks/{task_id}/download.zip")
    async def image_task_download_zip(
        task_id: str,
        viewer: ViewerContext = Depends(_viewer),
        db: Database = Depends(_db),
    ) -> Response:
        task = db.get_image_task(viewer.owner_id, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")
        histories = [
            item
            for item in db.list_history_by_task(viewer.owner_id, task_id)
            if item.get("status") == "succeeded" and item.get("image_path")
        ]
        archive = BytesIO()
        count = 0
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zip_file:
            for index, item in enumerate(histories, start=1):
                image_path = Path(str(item.get("image_path") or ""))
                if not image_path.is_file():
                    continue
                suffix = image_path.suffix.lower() if image_path.suffix else ".png"
                zip_file.write(image_path, f"{index:02d}-{str(item['id'])[:8]}{suffix}")
                count += 1
        if count == 0:
            raise HTTPException(status_code=404, detail="No downloadable images found for this task")
        archive.seek(0)
        headers = {"Content-Disposition": f'attachment; filename="joko-image2-{task_id[:12]}.zip"'}
        return Response(content=archive.getvalue(), media_type="application/zip", headers=headers)

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
            raise HTTPException(status_code=exc.status_code, detail=_provider_error_message(exc)) from exc
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
            raise HTTPException(status_code=exc.status_code, detail=_provider_error_message(exc)) from exc
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

    @app.post("/api/ecommerce/analyze")
    async def ecommerce_analyze(
        image: Annotated[UploadFile, File()],
        reference_image: Annotated[list[UploadFile] | None, File()] = None,
        product_name: Annotated[str, Form(max_length=300)] = "",
        materials: Annotated[str, Form(max_length=1200)] = "",
        selling_points: Annotated[str, Form(max_length=1600)] = "",
        scenarios: Annotated[str, Form(max_length=1200)] = "",
        platform: Annotated[str, Form(max_length=120)] = "",
        style: Annotated[str, Form(max_length=800)] = "",
        extra_requirements: Annotated[str, Form(max_length=1600)] = "",
        image_count: Annotated[int, Form(ge=1, le=9)] = 4,
        size: Annotated[str | None, Form()] = None,
        aspect_ratio: Annotated[str | None, Form()] = None,
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
        request_model = EcommerceAnalyzeRequest(
            product_name=product_name,
            materials=materials,
            selling_points=selling_points,
            scenarios=scenarios,
            platform=platform,
            style=style,
            extra_requirements=extra_requirements,
            image_count=image_count,
            size=size,
            aspect_ratio=aspect_ratio,
        )
        prompt = _ecommerce_prompt_from_fields(
            product_name=product_name,
            materials=materials,
            selling_points=selling_points,
            scenarios=scenarios,
            platform=platform,
            style=style,
            extra_requirements=extra_requirements,
            image_count=image_count,
        )
        try:
            analysis = await _analyze_ecommerce_product(
                provider,
                config,
                settings,
                upload=saved_upload,
                uploads=ecommerce_uploads,
                prompt=prompt,
                request=request_model,
            )
        except ProviderError as exc:
            raise HTTPException(status_code=exc.status_code, detail=_provider_error_message(exc)) from exc
        return {
            "analysis": analysis,
            "reference_notes": _task_reference_notes(ecommerce_uploads),
            "model": settings.prompt_optimizer_model.strip(),
            "form": _ecommerce_form_suggestion_from_analysis(analysis, request_model),
            "plans": _normalize_ecommerce_recommended_plans(analysis, request_model),
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
        selected_plan: Annotated[str | None, Form()] = None,
        analysis: Annotated[str | None, Form()] = None,
        viewer: ViewerContext = Depends(_viewer),
        db: Database = Depends(_db),
        settings: Settings = Depends(_settings),
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
        parsed_analysis = _parse_ecommerce_analysis(analysis)
        normalized_selected_plan = _normalize_selected_ecommerce_plan(_parse_selected_ecommerce_plan(selected_plan), n)
        provider_prompt = (
            _append_ecommerce_consistency_lock(prompt, parsed_analysis)
            if isinstance(parsed_analysis, dict)
            else prompt
        )
        provider_prompt = _append_reference_notes_to_prompt(provider_prompt, ecommerce_uploads)
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
                        "analysis": parsed_analysis,
                        "analysis_status": "ready" if isinstance(parsed_analysis, dict) else "pending",
                        "product_name": product_name,
                        "materials": materials,
                        "selling_points": selling_points,
                        "scenarios": scenarios,
                        "platform": platform,
                        "style": style,
                        "extra_requirements": extra_requirements,
                        "selected_plan": normalized_selected_plan,
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
            "trial_balance_usd": _effective_trial_balance_usd(settings_data, settings),
            "configured_trial_balance_usd": settings_data.get("trial_balance_usd"),
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


def _effective_trial_balance_usd(settings_data: dict[str, Any], settings: Settings) -> float:
    value = settings_data.get("trial_balance_usd")
    if value is None:
        return settings.trial_balance_usd
    try:
        return max(0, float(value))
    except (TypeError, ValueError):
        return settings.trial_balance_usd


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
    balance_usd = _effective_trial_balance_usd(site_settings or {}, settings)
    if not settings.trial_balance_grant_enabled or balance_usd <= 0:
        return 0, None
    configured_admin_token = str((site_settings or {}).get("sub2api_admin_token") or settings.sub2api_admin_token).strip()
    configured_admin_jwt = str((site_settings or {}).get("sub2api_admin_jwt") or settings.sub2api_admin_jwt).strip()
    token = configured_admin_token or configured_admin_jwt
    token_type = "api_key" if configured_admin_token else "jwt"
    if not token:
        return 0, "未配置 SUB2API_ADMIN_TOKEN 或 SUB2API_ADMIN_JWT，已创建试用 Key 但未自动赠送余额"
    payload = {
        "balance": balance_usd,
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
    return balance_usd, None


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
    selected_plan: dict[str, Any] | None = None,
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
    if selected_plan:
        context["selected_plan"] = _selected_plan_for_planner_context(selected_plan)
        user_text = (
            "请严格按 selected_plan 中已选定的系列蓝图扩写最终图像提示词。"
            "禁止改变屏数、顺序和每屏主题；只能补充构图、光线、材质、文字排版、商品一致性和电商质感。"
            "selected_plan.screens.internal_title 是内部模块名，不得要求图片把这些标题原样显示出来；"
            "可见文字应来自 copy、商品卖点、参数表或自然短句。\n\n"
            f"{json.dumps(context, ensure_ascii=False)}"
        )
    else:
        user_text = (
            "请把以下总需求拆解成系列图像提示词。"
            "每张图必须承担不同内容模块，但整体像同一套详情页/海报系列。\n\n"
            f"{json.dumps(context, ensure_ascii=False)}"
        )
    return {
        "model": settings.prompt_optimizer_model.strip(),
        "messages": [
            {"role": "system", "content": SERIES_PROMPT_PLANNER_SYSTEM_PROMPT},
            {"role": "user", "content": user_text},
        ],
        "temperature": 0.35,
        "max_tokens": 3800,
        "stream": False,
    }


def _selected_plan_for_planner_context(selected_plan: dict[str, Any]) -> dict[str, Any]:
    sanitized = dict(selected_plan)
    screens = selected_plan.get("screens")
    if not isinstance(screens, list):
        return sanitized
    sanitized_screens: list[dict[str, Any]] = []
    for screen in screens:
        if not isinstance(screen, dict):
            continue
        item = dict(screen)
        title = str(item.pop("title", "") or "").strip()
        if title:
            item["internal_title"] = title
        sanitized_screens.append(item)
    sanitized["screens"] = sanitized_screens
    sanitized["visible_text_rule"] = "internal_title 只用于后台识别页面主题，禁止作为图片可见文字。"
    return sanitized


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
        "要求像真实商品详情页，不要每一屏都做成标题海报；标题可以是小栏目标签，也可以用参数表、标注线、局部放大、模特场景、多角度拼版来表达。",
        "每一屏内容不能重复，应混合覆盖主视觉、模特/场景、材质细节、参数规格、尺码/尺寸、细节工艺、多角度展示、信任背书或转化总结。",
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
    request: EcommerceAnalyzeRequest | None = None,
) -> dict[str, Any]:
    reference_uploads = uploads or [upload]
    context: dict[str, Any] = {}
    if request is not None:
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
    content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                "请综合识别这些商品参考图，并结合以下电商详情页需求输出结构化商品分析。\n"
                "多张图代表同一个商品的不同角度或细节，必须合并为完整商品身份，不要只看第一张。\n\n"
                f"{_reference_notes_text(reference_uploads)}\n\n"
                f"用户填写字段：\n{json.dumps(context, ensure_ascii=False)}\n\n"
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


def _parse_selected_ecommerce_plan(value: str | None) -> dict[str, Any] | None:
    if not value:
        return None
    try:
        payload = json.loads(value)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        plan = EcommerceSelectedPlan.model_validate(payload)
    except ValidationError:
        return None
    return plan.model_dump()


def _parse_ecommerce_analysis(value: str | None) -> dict[str, Any] | None:
    if not value:
        return None
    try:
        payload = json.loads(value)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    if not any(
        key in payload
        for key in [
            "product_type",
            "appearance",
            "visible_material",
            "generation_constraints",
            "selling_points",
            "recommended_plans",
        ]
    ):
        return None
    return payload


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
    selected_plan: dict[str, Any] | None = None,
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
                selected_plan=selected_plan,
            ),
        )
        text = _extract_chat_completion_text(provider_response)
        plan = _parse_series_prompt_plan(text, image_count)
        if plan is not None:
            if selected_plan:
                plan = _merge_selected_plan_screen_metadata(plan, selected_plan)
            plan["source"] = "selected_plan" if selected_plan else "planner"
            return plan
    except ProviderError as exc:
        if _should_surface_provider_error(exc):
            raise
    except Exception:
        pass
    if selected_plan:
        plan = _fallback_selected_plan_prompt_plan(
            selected_plan=selected_plan,
            prompt=prompt,
            mode=mode,
            image_count=image_count,
            size=size,
            aspect_ratio=aspect_ratio,
            quality=quality,
        )
        plan["source"] = "selected_plan_fallback"
    else:
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


def _merge_selected_plan_screen_metadata(plan: dict[str, Any], selected_plan: dict[str, Any]) -> dict[str, Any]:
    plan_items = plan.get("items")
    screens = selected_plan.get("screens") if isinstance(selected_plan, dict) else None
    if not isinstance(plan_items, list) or not isinstance(screens, list):
        return plan
    merged_items: list[dict[str, Any]] = []
    for index, item in enumerate(plan_items):
        if not isinstance(item, dict):
            continue
        selected_screen = screens[index] if index < len(screens) and isinstance(screens[index], dict) else {}
        normalized_screen = _normalize_ecommerce_screen(
            {
                "title": selected_screen.get("title") or item.get("title") or f"第 {index + 1} 屏",
                "copy": item.get("copy") or selected_screen.get("copy") or "",
                "layout_type": selected_screen.get("layout_type") or item.get("layout_type"),
                "visual_goal": selected_screen.get("visual_goal") or item.get("visual_goal"),
                "copy_density": selected_screen.get("copy_density") or item.get("copy_density"),
                "needs_model": selected_screen.get("needs_model") if selected_screen.get("needs_model") is not None else item.get("needs_model"),
                "needs_specs": selected_screen.get("needs_specs") if selected_screen.get("needs_specs") is not None else item.get("needs_specs"),
                "needs_closeup": selected_screen.get("needs_closeup") if selected_screen.get("needs_closeup") is not None else item.get("needs_closeup"),
                "reference_focus": selected_screen.get("reference_focus") or item.get("reference_focus"),
            },
            index=index,
        )
        merged_items.append({**item, **normalized_screen, "index": item.get("index") or index + 1})
    return {**plan, "items": merged_items}


async def _analyze_ecommerce_product(
    provider: OpenAICompatibleImageClient,
    config: dict[str, Any],
    settings: Settings,
    *,
    upload: dict[str, Any],
    uploads: list[dict[str, Any]] | None = None,
    prompt: str,
    request: EcommerceAnalyzeRequest | None = None,
) -> dict[str, Any]:
    try:
        provider_response = await provider.chat_completion(
            config,
            _ecommerce_product_analyzer_payload(upload=upload, uploads=uploads, prompt=prompt, settings=settings, request=request),
        )
        parsed = _extract_json_object(_extract_chat_completion_text(provider_response))
        if isinstance(parsed, dict):
            parsed["source"] = "vision"
            return parsed
    except ProviderError:
        raise
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
        "selling_points": _split_ecommerce_field(request.selling_points if request else ""),
        "target_audience": [],
        "use_scenarios": _split_ecommerce_field(request.scenarios if request else ""),
        "style_suggestions": _split_ecommerce_field(request.style if request else "") or ["高级、干净、统一电商详情页"],
        "generation_constraints": "严格参考上传商品图，保持同一商品主体、颜色、材质、比例、结构和轮廓一致。",
        "recommended_plans": [],
    }


def _split_ecommerce_field(value: str | None) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    for separator in ["|", "，", "、", ",", "\n", "；", ";"]:
        text = text.replace(separator, "\n")
    return [item.strip() for item in text.splitlines() if item.strip()][:12]


def _join_ecommerce_values(values: Any) -> str:
    if isinstance(values, list):
        return "、".join(str(item).strip() for item in values if str(item).strip())
    if isinstance(values, str):
        return values.strip()
    return ""


def _ecommerce_form_suggestion_from_analysis(analysis: dict[str, Any], request: EcommerceAnalyzeRequest) -> dict[str, Any]:
    product_type = str(analysis.get("product_type") or "").strip()
    appearance = str(analysis.get("appearance") or "").strip()
    visible_material = str(analysis.get("visible_material") or "").strip()
    selling_points = _join_ecommerce_values(analysis.get("selling_points")) or request.selling_points.strip()
    scenarios = _join_ecommerce_values(analysis.get("use_scenarios")) or request.scenarios.strip()
    styles = _join_ecommerce_values(analysis.get("style_suggestions"))
    details = _join_ecommerce_values(analysis.get("details"))
    colors = _join_ecommerce_values(analysis.get("colors"))
    default_extra = "严格保持商品外观一致，不改变颜色、材质、比例、结构和关键细节。"
    if details or colors or appearance:
        default_extra = f"{default_extra} 商品识别重点：{appearance} {colors} {details}".strip()
    return {
        "product_name": request.product_name.strip() or product_type or "未命名商品",
        "materials": request.materials.strip() or visible_material,
        "selling_points": selling_points,
        "scenarios": scenarios,
        "platform": request.platform.strip() or "淘宝/抖音/小红书",
        "style": request.style.strip() or styles or "高级、干净、统一电商详情页",
        "extra_requirements": request.extra_requirements.strip() or default_extra,
        "image_count": request.image_count,
    }


def _normalize_ecommerce_recommended_plans(analysis: dict[str, Any], request: EcommerceAnalyzeRequest) -> list[dict[str, Any]]:
    raw_plans = analysis.get("recommended_plans")
    plans: list[dict[str, Any]] = []
    if isinstance(raw_plans, list):
        for raw in raw_plans[:6]:
            if not isinstance(raw, dict):
                continue
            plan = _normalize_ecommerce_plan(raw, analysis, request)
            if plan:
                plans.append(plan)
    fallback_plans = _fallback_ecommerce_recommended_plans(analysis, request)
    for fallback in fallback_plans:
        if len(plans) >= 3:
            break
        if not any(plan["name"] == fallback["name"] for plan in plans):
            plans.append(fallback)
    return plans[:3]


def _normalize_ecommerce_plan(raw: dict[str, Any], analysis: dict[str, Any], request: EcommerceAnalyzeRequest) -> dict[str, Any] | None:
    name = str(raw.get("name") or "").strip()
    if not name:
        return None
    image_count = max(1, min(9, request.image_count or 4))
    try:
        raw_image_count = int(raw.get("image_count") or 0)
    except (TypeError, ValueError):
        raw_image_count = 0
    if raw_image_count and raw_image_count != image_count:
        return None
    if _plan_name_mentions_inconsistent_count(name, image_count):
        return None
    screens = raw.get("screens")
    normalized_screens: list[dict[str, Any]] = []
    if not isinstance(screens, list) or len(screens) != image_count:
        return None
    for index, screen in enumerate(screens):
        if not isinstance(screen, dict):
            return None
        normalized_screen = _normalize_ecommerce_screen(screen, index=index)
        title = str(normalized_screen.get("title") or "").strip()
        copy = str(normalized_screen.get("copy") or "").strip()
        if not title and not copy:
            return None
        normalized_screens.append(normalized_screen)
    return {
        "name": name,
        "platform": str(raw.get("platform") or request.platform or "通用电商").strip(),
        "style": str(raw.get("style") or request.style or "高级、干净、统一电商详情页").strip(),
        "image_count": image_count,
        "materials": str(raw.get("materials") or request.materials or analysis.get("visible_material") or "").strip(),
        "selling_points": str(raw.get("selling_points") or request.selling_points or _join_ecommerce_values(analysis.get("selling_points"))).strip(),
        "scenarios": str(raw.get("scenarios") or request.scenarios or _join_ecommerce_values(analysis.get("use_scenarios"))).strip(),
        "extra_requirements": str(raw.get("extra_requirements") or request.extra_requirements or "").strip(),
        "reason": str(raw.get("reason") or "").strip(),
        "screens": normalized_screens,
    }


def _normalize_ecommerce_screen(screen: dict[str, Any], *, index: int = 0) -> dict[str, Any]:
    title = str(screen.get("title") or "").strip() or "内容模块"
    copy = str(screen.get("copy") or screen.get("body_copy") or "").strip()
    layout_type = _normalize_ecommerce_layout_type(screen.get("layout_type"), title, copy, index)
    visual_goal = str(screen.get("visual_goal") or "").strip()
    if not visual_goal:
        visual_goal = _default_visual_goal_for_layout(layout_type, title, copy)
    copy_density = str(screen.get("copy_density") or "").strip().lower()
    if copy_density not in {"low", "medium", "high"}:
        copy_density = _default_copy_density_for_layout(layout_type)
    reference_focus = screen.get("reference_focus")
    if isinstance(reference_focus, list):
        normalized_focus = [str(item).strip() for item in reference_focus if str(item).strip()][:8]
    else:
        normalized_focus = []
    return {
        "title": title,
        "copy": copy,
        "layout_type": layout_type,
        "visual_goal": visual_goal,
        "copy_density": copy_density,
        "needs_model": _bool_or_default(screen.get("needs_model"), layout_type in {"model_fit", "scene_lifestyle"}),
        "needs_specs": _bool_or_default(screen.get("needs_specs"), layout_type in {"spec_table", "size_chart"}),
        "needs_closeup": _bool_or_default(screen.get("needs_closeup"), layout_type in {"material_closeup", "detail_callout"}),
        "reference_focus": normalized_focus,
    }


def _normalize_ecommerce_layout_type(value: Any, title: str, copy: str, index: int = 0) -> str:
    text = str(value or "").strip().lower()
    aliases = {
        "hero": "hero",
        "main_visual": "hero",
        "cover": "social_cover",
        "social_cover": "social_cover",
        "model": "model_fit",
        "model_fit": "model_fit",
        "try_on": "model_fit",
        "scene": "scene_lifestyle",
        "scene_lifestyle": "scene_lifestyle",
        "lifestyle": "scene_lifestyle",
        "material": "material_closeup",
        "material_closeup": "material_closeup",
        "closeup": "material_closeup",
        "detail": "detail_callout",
        "detail_callout": "detail_callout",
        "spec": "spec_table",
        "spec_table": "spec_table",
        "parameter": "spec_table",
        "size": "size_chart",
        "size_chart": "size_chart",
        "multi_angle": "multi_angle",
        "angle": "multi_angle",
        "comparison": "comparison",
        "compare": "comparison",
        "conversion": "conversion",
        "summary": "conversion",
    }
    if text in aliases:
        return aliases[text]
    combined = f"{title} {copy}".lower()
    if any(keyword in combined for keyword in ["模特", "上身", "试穿", "穿搭"]):
        return "model_fit"
    if any(keyword in combined for keyword in ["场景", "生活", "通勤", "出街", "家居", "使用"]):
        return "scene_lifestyle"
    if any(keyword in combined for keyword in ["材质", "面料", "纹理", "果肉", "质感"]):
        return "material_closeup"
    if any(keyword in combined for keyword in ["细节", "工艺", "领口", "袖口", "下摆", "接口", "局部"]):
        return "detail_callout"
    if any(keyword in combined for keyword in ["参数", "规格", "尺寸", "尺码", "成分"]):
        return "spec_table"
    if any(keyword in combined for keyword in ["角度", "正面", "侧面", "背面", "多角度"]):
        return "multi_angle"
    if any(keyword in combined for keyword in ["对比", "比较", "差异"]):
        return "comparison"
    if any(keyword in combined for keyword in ["总结", "下单", "转化", "购买", "收尾"]):
        return "conversion"
    if index == 0:
        return "hero"
    return "detail_callout"


def _default_visual_goal_for_layout(layout_type: str, title: str, copy: str) -> str:
    goals = {
        "hero": "用商品主视觉建立第一眼认知，保留核心卖点但不要堆满大字。",
        "social_cover": "做适合内容平台首图的吸引力封面，文字少、画面有记忆点。",
        "model_fit": "展示真人或模特上身效果，重点看版型、比例、穿着状态和商品关键图案/结构。",
        "scene_lifestyle": "把商品放入真实使用或穿搭场景，突出适用人群和使用氛围。",
        "material_closeup": "用微距或局部放大展示材质、纹理、触感和可见品质。",
        "detail_callout": "用标注线、局部放大框和少量信息卡说明关键结构或工艺细节。",
        "spec_table": "用参数表、规格卡或信息图表达关键规格，不要做成大标题海报。",
        "size_chart": "用尺码表、尺寸示意、身高体重建议或适配范围表达购买决策信息。",
        "multi_angle": "用正面、侧面、背面或平铺组合展示商品完整外观。",
        "comparison": "用对比栏、选择建议或差异卡片帮助用户快速判断。",
        "conversion": "做详情页结尾总结，强化适合人群和购买理由，画面简洁有收束感。",
    }
    return goals.get(layout_type, copy or title)


def _default_copy_density_for_layout(layout_type: str) -> str:
    if layout_type in {"spec_table", "size_chart", "comparison"}:
        return "high"
    if layout_type in {"material_closeup", "detail_callout", "multi_angle"}:
        return "medium"
    return "low"


def _bool_or_default(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    return default


def _plan_name_mentions_inconsistent_count(name: str, image_count: int) -> bool:
    text = str(name or "")
    if not text:
        return False
    digit_counts = {
        "1": 1,
        "2": 2,
        "3": 3,
        "4": 4,
        "5": 5,
        "6": 6,
        "7": 7,
        "8": 8,
        "9": 9,
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    for token, count in digit_counts.items():
        if f"{token}屏" in text or f"{token}张" in text or f"{token}图" in text:
            return count != image_count
    return False


def _normalize_selected_ecommerce_plan(plan: dict[str, Any] | None, image_count: int) -> dict[str, Any] | None:
    if not isinstance(plan, dict):
        return None
    try:
        requested_count = max(1, min(9, int(image_count or plan.get("image_count") or 1)))
    except (TypeError, ValueError):
        requested_count = 1
    request = EcommerceAnalyzeRequest(
        product_name="",
        materials=str(plan.get("materials") or ""),
        selling_points=str(plan.get("selling_points") or ""),
        scenarios=str(plan.get("scenarios") or ""),
        platform=str(plan.get("platform") or ""),
        style=str(plan.get("style") or ""),
        extra_requirements=str(plan.get("extra_requirements") or ""),
        image_count=requested_count,
    )
    normalized = _normalize_ecommerce_plan(plan, {}, request)
    if normalized is None:
        return None
    normalized["image_count"] = requested_count
    return normalized


def _fallback_ecommerce_recommended_plans(analysis: dict[str, Any], request: EcommerceAnalyzeRequest) -> list[dict[str, Any]]:
    product_name = request.product_name.strip() or str(analysis.get("product_type") or "商品").strip() or "商品"
    materials = request.materials.strip() or str(analysis.get("visible_material") or "").strip()
    selling_points = request.selling_points.strip() or _join_ecommerce_values(analysis.get("selling_points")) or "外观质感、实用价值、细节做工"
    scenarios = request.scenarios.strip() or _join_ecommerce_values(analysis.get("use_scenarios")) or "日常使用、送礼、居家/办公/出行场景"
    constraints = str(analysis.get("generation_constraints") or "").strip()
    base_extra = request.extra_requirements.strip() or f"严格保持{product_name}主体一致。{constraints}".strip()
    count = max(1, min(9, request.image_count or 4))
    category = _infer_ecommerce_category(analysis, request)
    detail_screens = _fallback_ecommerce_screens_for_category(count, category, "detail")
    social_screens = _fallback_ecommerce_screens_for_category(count, category, "social")
    simple_screens = _fallback_ecommerce_screens_for_category(count, category, "main")
    return [
        {
            "name": f"淘宝详情页 {count} 屏转化方案",
            "platform": request.platform.strip() or "淘宝/天猫/抖音商城",
            "style": request.style.strip() or "高级、干净、统一电商详情页",
            "image_count": count,
            "materials": materials,
            "selling_points": selling_points,
            "scenarios": scenarios,
            "extra_requirements": base_extra,
            "reason": "适合直接做商品详情页，按卖点、场景、材质和转化顺序展开。",
            "screens": detail_screens,
        },
        {
            "name": f"小红书种草 {count} 屏方案",
            "platform": "小红书/朋友圈",
            "style": "自然种草、真实生活感、干净明亮、统一排版",
            "image_count": count,
            "materials": materials,
            "selling_points": selling_points,
            "scenarios": scenarios,
            "extra_requirements": base_extra,
            "reason": "适合做内容平台发布，强调使用感、场景感和购买理由。",
            "screens": social_screens,
        },
        {
            "name": f"白底主图加场景 {count} 屏方案",
            "platform": request.platform.strip() or "淘宝/1688/独立站",
            "style": "白底主图、清晰产品展示、少量高级阴影、商业摄影质感",
            "image_count": count,
            "materials": materials,
            "selling_points": selling_points,
            "scenarios": scenarios,
            "extra_requirements": base_extra,
            "reason": "适合用户还没有明确风格时，先产出更稳的主图和基础卖点图。",
            "screens": simple_screens,
        },
    ]


def _infer_ecommerce_category(analysis: dict[str, Any], request: EcommerceAnalyzeRequest) -> str:
    text = " ".join(
        [
            request.product_name,
            request.materials,
            request.selling_points,
            request.scenarios,
            str(analysis.get("product_type") or ""),
            str(analysis.get("appearance") or ""),
            _join_ecommerce_values(analysis.get("details")),
        ]
    ).lower()
    if any(keyword in text for keyword in ["榴莲", "水果", "生鲜", "果肉", "食品", "茶", "咖啡", "零食", "饮品"]):
        return "fresh_food"
    if any(keyword in text for keyword in ["t恤", "衣", "服装", "面料", "穿搭", "短袖", "裙", "裤", "鞋", "包"]):
        return "fashion"
    if any(keyword in text for keyword in ["插座", "地插", "电源", "数码", "手机", "耳机", "键盘", "设备", "电器"]):
        return "electronics"
    if any(keyword in text for keyword in ["抱枕", "家居", "沙发", "床", "家具", "收纳", "灯"]):
        return "home"
    return "general"


def _fallback_ecommerce_screens_for_category(image_count: int, category: str, plan_kind: str) -> list[dict[str, str]]:
    if category == "fresh_food":
        detail = [
            ("品种总览", "先把商品/品种/等级结构讲清楚，让用户快速知道这是什么。", "hero"),
            ("高端推荐", "突出高阶款、热门款或主推款，解释适合什么口味。", "comparison"),
            ("入门推荐", "给新手或大众用户一个不踩雷选择。", "comparison"),
            ("口感风味对比", "按甜度、香气、软糯度、浓郁度做横向比较。", "comparison"),
            ("颜色成熟度判断", "用颜色、纹理、果肉状态或外观特征说明怎么判断。", "material_closeup"),
            ("新鲜度/品质细节", "展示可见材质、细节、纹理和新鲜状态。", "detail_callout"),
            ("价格/规格参考", "说明不同规格、等级或预算怎么选。", "spec_table"),
            ("适合人群/场景", "说明送礼、家庭分享、尝鲜、直播讲解等使用场景。", "scene_lifestyle"),
            ("下单选择总结", "用简洁决策路径帮助用户完成购买选择。", "conversion"),
        ]
        social = [
            ("种草封面", "用一句话讲清核心吸引点，适合内容平台首图。", "social_cover"),
            ("为什么值得买", "用真实使用/品尝理由解释价值。", "scene_lifestyle"),
            ("口感体验", "突出味觉、质地、香气和满足感。", "material_closeup"),
            ("怎么挑不踩雷", "提供普通用户能理解的挑选方法。", "detail_callout"),
            ("细节实拍感", "强调真实材质、果肉或外观细节。", "material_closeup"),
            ("适合谁", "给不同口味、人群或预算推荐。", "comparison"),
            ("场景代入", "放到家庭、聚会、送礼、门店或直播场景中。", "scene_lifestyle"),
            ("对比总结", "把不同选择做成清晰对比。", "comparison"),
            ("行动收尾", "给出清晰购买建议。", "conversion"),
        ]
        main = [
            ("白底主视觉", "清楚展示商品主体和核心名称。", "hero"),
            ("核心卖点", "用最少文字说明为什么选它。", "detail_callout"),
            ("品种/规格", "展示主要品种、等级或规格差异。", "spec_table"),
            ("口感/风味", "解释用户最关心的体验差异。", "comparison"),
            ("细节特写", "突出真实质感和可见品质。", "material_closeup"),
            ("挑选方法", "给出简单判断标准。", "detail_callout"),
            ("场景用途", "说明适合什么销售或使用场景。", "scene_lifestyle"),
            ("信任说明", "用标准化信息增强专业感。", "spec_table"),
            ("购买总结", "收尾强化选择理由。", "conversion"),
        ]
    elif category == "fashion":
        detail = [
            ("主视觉上身", "展示商品整体版型和第一眼卖点。", "model_fit"),
            ("版型参数", "用肩宽、胸围、衣长、袖长等参数或版型标注说明穿着轮廓。", "spec_table"),
            ("正面图案细节", "用局部放大框展示印花/刺绣/图案位置、比例和工艺。", "detail_callout"),
            ("面料质感", "展示材质、纹理、舒适度和垂坠感。", "material_closeup"),
            ("场景穿搭", "展示通勤、约会、街头、居家等搭配场景。", "scene_lifestyle"),
            ("尺码建议", "用尺码表或身高体重建议说明适合身形。", "size_chart"),
            ("多角度展示", "展示正面、侧面、背面或平铺组合，说明商品完整外观。", "multi_angle"),
            ("百搭优势", "说明不同单品组合和适用季节。", "comparison"),
            ("转化总结", "强化购买理由和适合人群。", "conversion"),
        ]
        social = detail
        main = detail
    elif category == "electronics":
        detail = [
            ("产品主视觉", "展示产品外观和核心功能。", "hero"),
            ("核心功能", "解释最重要的使用价值。", "detail_callout"),
            ("结构细节", "展示接口、按键、材质、模块和尺寸。", "detail_callout"),
            ("安装/使用", "说明使用方式或安装场景。", "scene_lifestyle"),
            ("材质耐用", "突出材质、安全性、耐用性。", "material_closeup"),
            ("参数规格", "清晰列出尺寸、规格、适配范围。", "spec_table"),
            ("场景适配", "展示办公室、家用、商业等场景。", "scene_lifestyle"),
            ("对比优势", "和常规方案做差异说明。", "comparison"),
            ("购买总结", "收尾说明适合谁购买。", "conversion"),
        ]
        social = detail
        main = detail
    elif category == "home":
        detail = [
            ("家居主视觉", "展示商品整体和家居氛围。", "hero"),
            ("舒适体验", "说明触感、支撑、使用感。", "scene_lifestyle"),
            ("材质细节", "展示面料、填充、纹理或结构。", "material_closeup"),
            ("使用场景", "展示沙发、床头、办公、休闲等场景。", "scene_lifestyle"),
            ("尺寸适配", "说明尺寸、定制、适配范围。", "spec_table"),
            ("细节工艺", "突出边角、走线、结构或耐用性。", "detail_callout"),
            ("搭配优势", "展示与不同空间风格搭配。", "comparison"),
            ("人群需求", "说明适合家庭、租房、办公等人群。", "scene_lifestyle"),
            ("转化总结", "强化购买理由。", "conversion"),
        ]
        social = detail
        main = detail
    else:
        detail = [
            ("主视觉卖点", "突出产品核心利益点、主视觉和购买理由。", "hero"),
            ("使用场景", "展示产品在真实生活、电商或目标场景中的使用方式。", "scene_lifestyle"),
            ("材质/细节", "解释材质、触感、结构、工艺和品质细节。", "material_closeup"),
            ("功能优势", "说明用户最关心的功能或价值。", "detail_callout"),
            ("规格参数", "说明尺寸、规格、定制能力和适配范围。", "spec_table"),
            ("场景搭配", "展示和不同环境、风格、用途的搭配优势。", "scene_lifestyle"),
            ("细节特写", "用近景突出纹理、边缘、缝线、质感和细节。", "detail_callout"),
            ("信任背书", "强调品质保障、耐用性、售后或适合人群。", "comparison"),
            ("收尾转化", "做详情页结尾总结，强化购买行动。", "conversion"),
        ]
        social = detail
        main = detail
    titles = {"detail": detail, "social": social, "main": main}.get(plan_kind, detail)
    return _fallback_ecommerce_screens(image_count, titles)


def _fallback_ecommerce_screens(image_count: int, items: list[tuple[str, str, str] | tuple[str, str]]) -> list[dict[str, Any]]:
    screens = []
    for index in range(image_count):
        item = items[index] if index < len(items) else (f"第 {index + 1} 屏", "补充一个不重复的产品详情模块。", "detail_callout")
        title, copy = item[0], item[1]
        layout_type = item[2] if len(item) >= 3 else _normalize_ecommerce_layout_type(None, title, copy, index)
        screens.append(_normalize_ecommerce_screen({"title": title, "copy": copy, "layout_type": layout_type}, index=index))
    return screens


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


def _final_ecommerce_provider_prompt(prompt: str, plan_item: dict[str, Any], ecommerce_analysis: dict[str, Any] | None) -> str:
    text = str(prompt or "").strip()
    title = str(plan_item.get("title") or "").strip()
    copy = str(plan_item.get("copy") or "").strip()
    if title:
        text = text.replace(f"栏目标签：{title}", "栏目标签：内部模块名，不作为画面文字")
    rules = [
        "画面文字规则：",
        "后台模块名、方案屏幕名、栏目名只用于理解结构，禁止作为图片里的可见标题。",
        "不要在图片中写出类似“种草封面”“第一眼亮点”“主视觉”“材质”“第1屏方案标题”这类后台模块名。",
    ]
    if title:
        rules.append(f"本屏后台模块名是“{title}”，只能用于理解页面功能，不能原样显示在画面里。")
    if copy:
        rules.append(f"如需要可见文字，优先自然改写这句面向用户的文案：{copy}")
    if isinstance(ecommerce_analysis, dict):
        return _append_ecommerce_consistency_lock(f"{text}\n\n" + "\n".join(rules), ecommerce_analysis)
    return f"{text}\n\n" + "\n".join(rules)


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
        screen = _normalize_ecommerce_screen(item, index=index - 1)
        items.append(
            {
                "index": index,
                "title": screen["title"] or f"第 {index} 屏",
                "copy": screen["copy"],
                "layout_type": screen["layout_type"],
                "visual_goal": screen["visual_goal"],
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
        ("核心卖点", "突出产品核心利益点、主视觉和购买理由。", "hero"),
        ("使用场景", "展示产品在真实生活、电商或目标场景中的使用方式。", "scene_lifestyle"),
        ("材质工艺", "解释材质、触感、结构、工艺和品质细节。", "material_closeup"),
        ("成分结构", "拆解填充、面料、内部结构或关键参数。", "detail_callout"),
        ("尺寸定制", "说明尺寸、规格、定制能力和适配范围。", "spec_table"),
        ("百搭优势", "展示和不同环境、风格、用途的搭配优势。", "comparison"),
        ("细节特写", "用近景突出纹理、边缘、缝线、质感和细节。", "detail_callout"),
        ("信任背书", "强调品质保障、耐用性、售后或适合人群。", "comparison"),
        ("收尾转化", "做详情页结尾总结，强化品牌感和购买行动。", "conversion"),
    ]
    style_guide = (
        f"统一系列视觉：{aspect_ratio} 竖版/横版构图按参数执行，{size}，{quality} quality；"
        "同一产品主体、同一色调、同一字体样式、同一文字层级和排版网格、同一电商详情页视觉系统；"
        "标题简洁可读，正文短句清晰，避免乱码和不一致的品牌符号。"
    )
    if mode == "edit":
        style_guide += " 严格参考上传图片中的产品主体、外观、材质和结构，保持产品一致性。"
    items = []
    for index in range(1, image_count + 1):
        title, copy, layout_type = modules[index - 1] if index <= len(modules) else (f"第 {index} 屏", "补充一个不重复的产品详情模块。", "detail_callout")
        screen = _normalize_ecommerce_screen({"title": title, "copy": copy, "layout_type": layout_type}, index=index - 1)
        prompt_parts = [
            f"{prompt.strip()}",
            f"这是同一套系列详情页的第 {index}/{image_count} 屏，页面类型：{screen['layout_type']}。",
            f"本屏内容说明：{screen['copy']}",
            f"本屏视觉目标：{screen['visual_goal']}",
            style_guide,
            _layout_prompt_instruction(screen),
            "本屏必须和其他屏保持统一色调、字体、产品比例和视觉语言，但内容模块不能重复。不要机械做成大标题卡片，不要把后台模块名写进图片。",
        ]
        if mode == "edit":
            prompt_parts.append("根据上传参考图中的同一个产品生成本屏，产品外观必须一致。")
        items.append({**screen, "index": index, "prompt": "\n".join(prompt_parts)})
    return {"style_guide": style_guide, "items": items}


def _fallback_selected_plan_prompt_plan(
    *,
    selected_plan: dict[str, Any],
    prompt: str,
    mode: str,
    image_count: int,
    size: str,
    aspect_ratio: str,
    quality: str,
) -> dict[str, Any]:
    screens = selected_plan.get("screens") if isinstance(selected_plan, dict) else []
    if not isinstance(screens, list):
        screens = []
    style = str(selected_plan.get("style") or "").strip()
    platform = str(selected_plan.get("platform") or "").strip()
    plan_name = str(selected_plan.get("name") or "").strip()
    style_guide = (
        f"严格按已选方案“{plan_name or '电商方案'}”生成；{aspect_ratio} 构图，{size}，{quality} quality；"
        f"平台方向：{platform or '通用电商'}；视觉风格：{style or '高级、干净、统一电商详情页'}；"
        "保持同一商品主体、同一色调、同一字体样式、同一文字层级和排版网格。"
    )
    if mode == "edit":
        style_guide += " 严格参考上传商品图，商品外观、颜色、材质、结构和比例必须一致。"
    items: list[dict[str, Any]] = []
    for index in range(1, image_count + 1):
        screen = screens[index - 1] if index - 1 < len(screens) and isinstance(screens[index - 1], dict) else {}
        normalized_screen = _normalize_ecommerce_screen(
            {
                "title": screen.get("title") or f"第 {index} 屏",
                "copy": screen.get("copy") or "按已选方案补充一个不重复的产品详情模块。",
                "layout_type": screen.get("layout_type"),
                "visual_goal": screen.get("visual_goal"),
                "copy_density": screen.get("copy_density"),
                "needs_model": screen.get("needs_model"),
                "needs_specs": screen.get("needs_specs"),
                "needs_closeup": screen.get("needs_closeup"),
                "reference_focus": screen.get("reference_focus"),
            },
            index=index - 1,
        )
        prompt_parts = [
            prompt.strip(),
            f"已选方案：{plan_name or '电商方案'}。",
            f"这是第 {index}/{image_count} 屏，必须严格对应页面类型：{normalized_screen['layout_type']}。",
            f"本屏内容说明：{normalized_screen['copy']}",
            f"本屏视觉目标：{normalized_screen['visual_goal']}",
            style_guide,
            _layout_prompt_instruction(normalized_screen),
            "不得改变本屏主题，不得重新拆分为其他内容；只允许扩写画面构图、商品展示、背景、文案层级和细节表达。不要每张都做成大标题海报，不要把后台模块名写进图片。",
        ]
        if mode == "edit":
            prompt_parts.append("根据上传参考图中的同一个产品生成本屏，产品外观必须一致。")
        items.append({**normalized_screen, "index": index, "prompt": "\n".join(prompt_parts)})
    return {"style_guide": style_guide, "items": items}


def _layout_prompt_instruction(screen: dict[str, Any]) -> str:
    layout_type = str(screen.get("layout_type") or "")
    copy_density = str(screen.get("copy_density") or "medium")
    instructions = {
        "hero": "构图要求：商品占画面主导，少量核心卖点信息卡，标题可以小而清晰，不要满屏文字。",
        "social_cover": "构图要求：封面感强，商品和使用结果有吸引力，文字控制在一句主张和少量标签。",
        "model_fit": "构图要求：使用真人或模特上身/使用展示，重点表现版型、比例、穿着状态，商品关键图案和结构必须完整可见。",
        "scene_lifestyle": "构图要求：真实场景化展示，环境服务于商品，不要让道具和文字遮挡主体。",
        "material_closeup": "构图要求：局部微距、质感放大、材质纹理清楚，可用小标注线，避免大面积标题。",
        "detail_callout": "构图要求：局部放大框、标注线、信息卡说明细节，商品主体和细节区域同时可见。",
        "spec_table": "构图要求：参数表/规格卡/信息图为主，数据分组清晰，商品在旁辅助展示，文字密度可以较高但要整齐。",
        "size_chart": "构图要求：尺码表、尺寸示意线、身高体重建议或适配范围清楚，不要只放一句标题。",
        "multi_angle": "构图要求：正面、侧面、背面或平铺多角度组合，比例统一，标注少而准。",
        "comparison": "构图要求：用左右对比、分栏选择建议或差异卡片表达，不要变成单张口号海报。",
        "conversion": "构图要求：详情页结尾收束，商品、适合人群、购买理由三者清楚，文字简短有购买决策感。",
    }
    density_rules = {
        "low": "文字策略：低文字密度，1 句面向用户的短文案或少量卖点标签以内，不使用后台模块名。",
        "medium": "文字策略：中文字密度，可使用 2-4 个短标签或标注点。",
        "high": "文字策略：高信息密度，适合表格/参数/对比，但必须整齐可读。",
    }
    return "\n".join(
        [
            instructions.get(layout_type, "构图要求：按本屏页面类型做真实详情页模块，避免重复标题卡片。"),
            density_rules.get(copy_density, density_rules["medium"]),
        ]
    )


async def _ensure_ecommerce_analysis_for_task(
    db: Database,
    settings: Settings,
    provider: OpenAICompatibleImageClient,
    *,
    task_id: str,
    request_payload: dict[str, Any],
    prompt: str,
    config: dict[str, Any],
    image_count: int,
) -> dict[str, Any]:
    ecommerce = request_payload.get("ecommerce")
    if not isinstance(ecommerce, dict):
        return {}
    existing = ecommerce.get("analysis")
    if isinstance(existing, dict):
        return existing

    uploads = request_payload.get("uploads")
    if not isinstance(uploads, list) or not uploads:
        return {}
    db.update_image_task(
        task_id,
        {
            "result": {
                "count_requested": image_count,
                "count_succeeded": 0,
                "ecommerce_analysis": None,
                "stage": "analyzing",
                "selected_plan": ecommerce.get("selected_plan") if isinstance(ecommerce.get("selected_plan"), dict) else None,
                "usage": [],
                "partial_errors": [],
            },
        },
    )
    request = EcommerceAnalyzeRequest(
        product_name=str(ecommerce.get("product_name") or ""),
        materials=str(ecommerce.get("materials") or ""),
        selling_points=str(ecommerce.get("selling_points") or ""),
        scenarios=str(ecommerce.get("scenarios") or ""),
        platform=str(ecommerce.get("platform") or ""),
        style=str(ecommerce.get("style") or ""),
        extra_requirements=str(ecommerce.get("extra_requirements") or ""),
        image_count=image_count,
        size=str(request_payload.get("fields", {}).get("size") or "") if isinstance(request_payload.get("fields"), dict) else "",
        aspect_ratio="",
    )
    analysis = await _analyze_ecommerce_product(
        provider,
        config,
        settings,
        upload=uploads[0],
        uploads=uploads,
        prompt=prompt,
        request=request,
    )
    ecommerce["analysis"] = analysis
    ecommerce["analysis_status"] = "ready"
    request_payload["ecommerce"] = ecommerce
    fields = request_payload.get("fields")
    if isinstance(fields, dict):
        fields["prompt"] = _append_reference_notes_to_prompt(
            _append_ecommerce_consistency_lock(prompt, analysis),
            uploads,
        )
        request_payload["fields"] = fields
    db.update_image_task(task_id, {"request": request_payload})
    return analysis


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
        if cleaned_size.lower() not in ALLOWED_PRESET_DIMENSIONS and width * height < 1024 * 1024:
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
        if tier in {"FAST", "1K", "2K", "4K"}:
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
    if tier == "FAST":
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


def _provider_error_message(exc: ProviderError) -> str:
    return _normalize_error_message(exc.message, exc.payload)


def _exception_message(exc: Exception) -> str:
    if isinstance(exc, ProviderError):
        return _provider_error_message(exc)
    message = str(exc).strip()
    if message:
        return _normalize_error_message(message)
    return _normalize_error_message(exc.__class__.__name__)


def _normalize_error_message(message: Any, payload: Any | None = None) -> str:
    text = str(message or "").strip()
    payload_type = ""
    payload_message = ""
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            payload_type = str(error.get("type") or "").strip()
            payload_message = str(error.get("message") or "").strip()
        else:
            payload_type = str(payload.get("type") or "").strip()
            payload_message = str(payload.get("message") or payload.get("error") or "").strip()

    combined = " ".join(item for item in [payload_type, payload_message, text] if item).lower()
    if "insufficient" in combined and "balance" in combined:
        return "余额不足，请充值或更换 API Key 后重试"
    if "billing_error" in combined and "balance" in combined:
        return "余额不足，请充值或更换 API Key 后重试"
    if "quota" in combined and ("exceeded" in combined or "insufficient" in combined):
        return "额度不足，请充值或更换 API Key 后重试"
    if text:
        return text
    if payload_message:
        return payload_message
    if payload_type:
        return payload_type
    return "任务失败，请稍后重试"


def _first_partial_error_message(partial_errors: list[dict[str, Any]]) -> str:
    for item in partial_errors:
        message = _normalize_error_message(item.get("error"), item.get("provider_response"))
        if message:
            return message
    return "图片批量生成失败"


def _batch_partial_error_message(partial_errors: list[dict[str, Any]]) -> str:
    first_message = _first_partial_error_message(partial_errors)
    count = len(partial_errors)
    if count <= 0:
        return ""
    return f"{count} 张图片生成失败：{first_message}"


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
            is_ecommerce_create = isinstance(request_payload.get("ecommerce"), dict) and not request_payload.get("source_history_id")
            if requested_count > 1 or is_ecommerce_create:
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
        message = _provider_error_message(exc)
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
                "result": {"error": message, "usage": None},
                "error": message,
            },
        )
    except Exception as exc:
        message = _exception_message(exc)
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
                "result": {"error": message, "usage": None},
                "error": message,
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
        if not isinstance(ecommerce_analysis, dict) and image_files:
            ecommerce_analysis = await _ensure_ecommerce_analysis_for_task(
                db,
                settings,
                provider,
                task_id=task_id,
                request_payload=request_payload,
                prompt=latest_for_plan["prompt"],
                config=config,
                image_count=image_count,
            )
            ecommerce_context = request_payload.get("ecommerce")
    selected_plan = None
    if isinstance(ecommerce_context, dict):
        selected_plan = _normalize_selected_ecommerce_plan(
            ecommerce_context.get("selected_plan") if isinstance(ecommerce_context.get("selected_plan"), dict) else None,
            image_count,
        )
    reference_note_prompt = _reference_notes_text(request_payload.get("uploads") or [])
    planning_prompt = latest_for_plan["prompt"]
    if isinstance(ecommerce_context, dict) and not isinstance(ecommerce_analysis, dict):
        db.update_image_task(
            task_id,
            {
                "result": {
                    "count_requested": image_count,
                    "count_succeeded": 0,
                    "ecommerce_analysis": None,
                    "stage": "analyzing",
                    "selected_plan": None,
                    "usage": [],
                    "partial_errors": [],
                },
            },
        )
    if isinstance(ecommerce_analysis, dict):
        planning_prompt = (
            f"{planning_prompt}\n\n"
            "商品图识别结果：\n"
            f"{json.dumps(ecommerce_analysis, ensure_ascii=False)}"
        )
    if isinstance(selected_plan, dict):
        planning_prompt = (
            f"{planning_prompt}\n\n"
            "用户已选定电商方案蓝图，最终每一屏必须严格按该方案 screens 执行：\n"
            f"{json.dumps(selected_plan, ensure_ascii=False)}"
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
        selected_plan=selected_plan,
    )
    plan_items = plan.get("items") if isinstance(plan, dict) else []
    if not isinstance(plan_items, list) or len(plan_items) != image_count:
        if selected_plan:
            plan = _fallback_selected_plan_prompt_plan(
                selected_plan=selected_plan,
                prompt=planning_prompt,
                mode=latest_for_plan["mode"],
                image_count=image_count,
                size=latest_for_plan["size"],
                aspect_ratio=latest_for_plan.get("aspect_ratio") or "",
                quality=latest_for_plan["quality"],
            )
            plan["source"] = "selected_plan_fallback"
        else:
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
                    "stage": "planning",
                    "selected_plan": selected_plan,
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
            _final_ecommerce_provider_prompt(item_prompt, plan_item, ecommerce_analysis)
            if isinstance(ecommerce_context, dict)
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
                replace_history_id=_replace_history_id_for_task(request_payload, latest_task) if image_count == 1 else None,
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
                        "stage": "generating",
                        "selected_plan": selected_plan,
                        "series_plan": _public_series_plan(plan),
                        "usage": usage_items,
                        "partial_errors": partial_errors,
                    },
                },
            )
        except ProviderError as exc:
            partial_errors.append({"index": index, "error": _provider_error_message(exc), "provider_response": exc.payload})
        except Exception as exc:
            partial_errors.append({"index": index, "error": _exception_message(exc), "provider_response": None})

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
                    "stage": "completed",
                    "selected_plan": selected_plan,
                    "series_plan": _public_series_plan(plan),
                    "partial_errors": partial_errors,
                },
                "error": None if not partial_errors else _batch_partial_error_message(partial_errors),
            },
        )
        return

    message = _first_partial_error_message(partial_errors) if partial_errors else "图片批量生成失败"
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
                "stage": "failed",
                "selected_plan": selected_plan,
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
                "layout_type": item.get("layout_type") or "",
                "visual_goal": item.get("visual_goal") or "",
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
    if _is_billing_provider_error(exc):
        return False
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
    if error_type in {"upstream_error", "rate_limit_error", "server_error"}:
        return True
    return "upstream" in lowered or "temporarily unavailable" in lowered


def _is_billing_provider_error(exc: ProviderError) -> bool:
    return _normalize_error_message(exc.message, exc.payload) in {
        "余额不足，请充值或更换 API Key 后重试",
        "额度不足，请充值或更换 API Key 后重试",
    }


def _should_surface_provider_error(exc: ProviderError) -> bool:
    if _is_billing_provider_error(exc):
        return True
    if exc.status_code in {400, 401, 402, 403}:
        return True
    return False


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
        return {"ok": False, "remaining": None, "message": _provider_error_message(exc), "raw": exc.payload}


app = create_app()
