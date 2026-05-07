from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.inspirations import cache_inspiration_images, normalize_inspiration_source_url, parse_inspiration_markdown
from app.main import create_app, _auth_client, _db, _image_size_tier, _provider, _provider_image_size, _settings
from app.provider import ProviderError
from app.settings import Settings


PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


class FakeProvider:
    def __init__(self) -> None:
        self.generated_configs: list[dict[str, Any]] = []
        self.generated_payloads: list[dict[str, Any]] = []
        self.chat_configs: list[dict[str, Any]] = []
        self.chat_payloads: list[dict[str, Any]] = []
        self.edited_configs: list[dict[str, Any]] = []
        self.edited_fields: list[dict[str, Any]] = []
        self.edited_images: list[list[tuple[str, bytes, str]]] = []

    async def test_connection(self, config: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True, "models": ["gpt-image-2"], "raw": {"data": [{"id": "gpt-image-2"}]}}

    async def usage(self, config: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True, "remaining": 12.5, "raw": {"remaining": 12.5, "unit": "USD"}}

    async def generate_image(self, config: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        await asyncio.sleep(0.02)
        assert payload["model"] == "gpt-image-2"
        self.generated_configs.append(dict(config))
        self.generated_payloads.append(payload)
        return {"created": 123, "data": [{"b64_json": PNG_B64, "revised_prompt": "revised"}], "usage": {"total_tokens": 1}}

    async def chat_completion(self, config: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        if not config.get("api_key"):
            raise ProviderError(400, "请先在配置页保存 JokoAI API Key")
        self.chat_configs.append(dict(config))
        self.chat_payloads.append(payload)
        system_content = payload["messages"][0]["content"] if payload.get("messages") else ""
        if "案例库搜索助手" in system_content:
            return {
                "id": "chatcmpl-ai-search-test",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(
                                {"query": "赛博 城市 夜景", "keywords": ["赛博", "城市", "夜景"]},
                                ensure_ascii=False,
                            ),
                        }
                    }
                ],
                "usage": {"total_tokens": 8},
            }
        if "电商商品图识别分析师" in system_content:
            return {
                "id": "chatcmpl-ecommerce-vision-test",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "product_type": "抱枕芯",
                                    "appearance": "白色方形蓬松抱枕芯",
                                    "visible_material": "细腻磨毛布料",
                                    "colors": ["奶油白"],
                                    "shape": "方形",
                                    "details": ["边角圆润", "表面柔软"],
                                    "generation_constraints": "保持白色方形抱枕芯主体、颜色、比例和布料质感一致",
                                },
                                ensure_ascii=False,
                            ),
                        }
                    }
                ],
                "usage": {"total_tokens": 18},
            }
        if "系列图像提示词规划师" in system_content:
            user_content = payload["messages"][1]["content"]
            count = 1
            mode = "generate"
            try:
                context = json.loads(user_content[user_content.index("{") : user_content.rindex("}") + 1])
                count = int(context.get("image_count") or 1)
                mode = str(context.get("mode") or "generate")
            except Exception:
                pass
            return {
                "id": "chatcmpl-series-test",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "style_guide": "统一奶油白电商详情页风格",
                                    "items": [
                                        {
                                            "index": index,
                                            "title": f"第{index}屏",
                                            "copy": f"{mode} 模块 {index}",
                                            "prompt": f"系列拆解提示词 {mode} 第{index}屏，统一风格，内容模块不同",
                                        }
                                        for index in range(1, count + 1)
                                    ],
                                },
                                ensure_ascii=False,
                            ),
                        }
                    }
                ],
                "usage": {"total_tokens": 24},
            }
        if "电商种草文案策划" in system_content:
            return {
                "id": "chatcmpl-ecommerce-copy-test",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "title": "抱枕芯详情页种草图",
                                    "body": "这组图围绕天鹅绒PP棉抱枕芯展开，重点突出柔软触感、蓬松回弹和可定做尺寸，适合放在沙发、床头和办公休憩场景里做种草展示。#电商设计 #小红书运营 #商品详情页",
                                },
                                ensure_ascii=False,
                            ),
                        }
                    }
                ],
                "usage": {"total_tokens": 20},
            }
        return {
            "id": "chatcmpl-test",
            "choices": [{"message": {"role": "assistant", "content": "优化后的淘宝机器人主页图提示词"}}],
            "usage": {"total_tokens": 12},
        }

    async def edit_image(
        self,
        config: dict[str, Any],
        fields: dict[str, Any],
        images: list[tuple[str, bytes, str]],
        mask: tuple[str, bytes, str] | None = None,
    ) -> dict[str, Any]:
        await asyncio.sleep(0.02)
        assert images
        self.edited_configs.append(dict(config))
        self.edited_fields.append(fields)
        self.edited_images.append(images)
        return {"created": 124, "data": [{"b64_json": PNG_B64}], "usage": {"total_tokens": 2}}


class FlakyProvider(FakeProvider):
    def __init__(self, generate_failures: int) -> None:
        super().__init__()
        self.generate_attempts = 0
        self.generate_failures = generate_failures

    async def generate_image(self, config: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        self.generate_attempts += 1
        if self.generate_attempts <= self.generate_failures:
            raise ProviderError(
                502,
                "Upstream request failed",
                {"error": {"message": "Upstream request failed", "type": "upstream_error"}},
            )
        return await super().generate_image(config, payload)


class FakeAuthClient:
    def __init__(self) -> None:
        self.public_settings_base_urls: list[str] = []
        self.register_base_urls: list[str] = []
        self.login_base_urls: list[str] = []
        self.login_2fa_base_urls: list[str] = []
        self.list_keys_base_urls: list[str] = []
        self.list_available_groups_base_urls: list[str] = []
        self.create_key_base_urls: list[str] = []
        self.list_usage_base_urls: list[str] = []
        self.payment_calls: list[dict[str, Any]] = []
        self.admin_balance_calls: list[dict[str, Any]] = []
        self.created_keys: list[dict[str, Any]] = []
        self.usage_logs: list[dict[str, Any]] = [
            {
                "id": 501,
                "request_id": "client:test",
                "model": "gpt-image-2",
                "actual_cost": 0.456,
                "total_cost": 0.456,
                "image_count": 1,
                "image_size": "2K",
                "inbound_endpoint": "/v1/images/generations",
                "billing_mode": "image",
                "created_at": "2026-04-26T10:00:00Z",
            }
        ]

    async def public_settings(self, base_url: str) -> dict[str, Any]:
        self.public_settings_base_urls.append(base_url)
        return {"registration_enabled": True, "email_verify_enabled": False, "backend_mode_enabled": False, "site_name": "demo"}

    async def send_verify_code(self, base_url: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {"message": "sent", "countdown": 60}

    async def register(self, base_url: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.register_base_urls.append(base_url)
        return {
            "access_token": "access-demo",
            "refresh_token": "refresh-demo",
            "token_type": "Bearer",
            "user": {"id": 7, "email": payload["email"], "username": "demo-user", "role": "admin"},
        }

    async def login(self, base_url: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.login_base_urls.append(base_url)
        return {
            "access_token": "access-demo",
            "refresh_token": "refresh-demo",
            "token_type": "Bearer",
            "user": {"id": 7, "email": payload["email"], "username": "demo-user", "role": "admin"},
        }

    async def login_2fa(self, base_url: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.login_2fa_base_urls.append(base_url)
        return {
            "access_token": "access-demo",
            "refresh_token": "refresh-demo",
            "token_type": "Bearer",
            "user": {"id": 7, "email": "demo@example.com", "username": "demo-user", "role": "admin"},
        }

    async def list_keys(self, base_url: str, access_token: str) -> list[dict[str, Any]]:
        self.list_keys_base_urls.append(base_url)
        return []

    async def list_available_groups(self, base_url: str, access_token: str) -> list[dict[str, Any]]:
        self.list_available_groups_base_urls.append(base_url)
        return [
            {"id": 11, "name": "openai-default", "platform": "openai", "status": "active"},
            {"id": 12, "name": "codex_plus", "platform": "openai", "status": "active"},
            {"id": 13, "name": "team", "platform": "openai", "status": "active"},
        ]

    async def create_key(self, base_url: str, access_token: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.create_key_base_urls.append(base_url)
        key = {
            "id": 99,
            "key": "sk-user-managed-123456",
            "name": payload["name"],
            "quota": payload.get("quota", 0),
            "expires_in_days": payload.get("expires_in_days"),
            "group": {"id": payload.get("group_id"), "name": "general-openai", "platform": "openai"},
            "status": "active",
        }
        self.created_keys.append(key)
        return key

    async def list_usage(self, base_url: str, access_token: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        self.list_usage_base_urls.append(base_url)
        return list(self.usage_logs)

    async def payment_checkout_info(self, base_url: str, access_token: str) -> dict[str, Any]:
        self.payment_calls.append({"method": "checkout_info", "base_url": base_url, "access_token": access_token})
        return {
            "methods": {
                "alipay": {
                    "daily_limit": 0,
                    "daily_used": 0,
                    "daily_remaining": 0,
                    "single_min": 1,
                    "single_max": 100,
                    "fee_rate": 0,
                    "available": True,
                }
            },
            "global_min": 1,
            "global_max": 100,
            "plans": [],
            "balance_disabled": False,
            "help_text": "demo help",
            "help_image_url": "",
            "stripe_publishable_key": "",
        }

    async def payment_create_order(self, base_url: str, access_token: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.payment_calls.append({"method": "create_order", "base_url": base_url, "access_token": access_token, "payload": payload})
        return {
            "order_id": 88,
            "amount": payload["amount"],
            "pay_amount": payload["amount"],
            "fee_rate": 0,
            "status": "PENDING",
            "payment_type": payload["payment_type"],
            "qr_code": "https://pay.example.com/qr/88",
            "expires_at": "2026-05-07T12:00:00Z",
        }

    async def payment_list_orders(self, base_url: str, access_token: str, params: dict[str, Any]) -> dict[str, Any]:
        self.payment_calls.append({"method": "list_orders", "base_url": base_url, "access_token": access_token, "params": params})
        return {"items": [], "total": 0, "page": params.get("page", 1), "page_size": params.get("page_size", 20)}

    async def payment_get_order(self, base_url: str, access_token: str, order_id: int) -> dict[str, Any]:
        self.payment_calls.append({"method": "get_order", "base_url": base_url, "access_token": access_token, "order_id": order_id})
        return {
            "id": order_id,
            "user_id": 7,
            "amount": 2,
            "pay_amount": 2,
            "fee_rate": 0,
            "payment_type": "alipay",
            "out_trade_no": "sub2_demo",
            "status": "COMPLETED",
            "order_type": "balance",
            "created_at": "2026-05-07T11:00:00Z",
            "expires_at": "2026-05-07T12:00:00Z",
            "refund_amount": 0,
        }

    async def payment_cancel_order(self, base_url: str, access_token: str, order_id: int) -> dict[str, Any]:
        self.payment_calls.append({"method": "cancel_order", "base_url": base_url, "access_token": access_token, "order_id": order_id})
        return {"message": "cancelled"}

    async def payment_verify_order(self, base_url: str, access_token: str, out_trade_no: str) -> dict[str, Any]:
        self.payment_calls.append({"method": "verify_order", "base_url": base_url, "access_token": access_token, "out_trade_no": out_trade_no})
        return {
            "id": 88,
            "user_id": 7,
            "amount": 2,
            "pay_amount": 2,
            "fee_rate": 0,
            "payment_type": "alipay",
            "out_trade_no": out_trade_no,
            "status": "COMPLETED",
            "order_type": "balance",
            "created_at": "2026-05-07T11:00:00Z",
            "expires_at": "2026-05-07T12:00:00Z",
            "refund_amount": 0,
        }

    async def admin_update_user_balance(
        self,
        base_url: str,
        admin_token: str,
        user_id: int,
        payload: dict[str, Any],
        *,
        token_type: str = "api_key",
    ) -> dict[str, Any]:
        self.admin_balance_calls.append(
            {
                "base_url": base_url,
                "admin_token": admin_token,
                "user_id": user_id,
                "payload": payload,
                "token_type": token_type,
            }
        )
        return {"id": user_id, "balance": payload["balance"]}


def make_app(tmp_path: Path, auth_client: FakeAuthClient | None = None, provider: FakeProvider | None = None):
    settings = Settings(
        backend_dir=tmp_path,
        database_path=tmp_path / "data" / "app.sqlite3",
        storage_dir=tmp_path / "storage",
        provider_base_url="http://127.0.0.1:9878/v1",
        auth_base_url="http://127.0.0.1:9878",
        provider_usage_path="/v1/usage",
        image_model="gpt-image-2",
        prompt_optimizer_model="gpt-5.5",
        default_size="2K",
        default_quality="auto",
        image_price_1k=0.134,
        image_price_2k=0.201,
        image_price_4k=0.268,
        user_name="tester",
        cors_origins=["http://127.0.0.1:3000"],
        request_timeout_seconds=10,
        inspiration_source_url="https://example.com/README.md",
        inspiration_sync_interval_seconds=0,
        inspiration_sync_on_startup=False,
        session_cookie_name="cybergen_session",
        guest_cookie_name="cybergen_guest",
        session_ttl_seconds=3600,
        guest_ttl_seconds=86400,
        cookie_secure=False,
        trial_key_enabled=True,
        trial_key_quota_usd=0,
        trial_key_expires_days=30,
        trial_key_name_prefix="joko-image2-trial",
        trial_balance_grant_enabled=True,
        trial_balance_usd=2,
        sub2api_admin_token="",
        sub2api_admin_jwt="",
        recharge_url="https://ai.get-money.locker",
    )
    app = create_app(settings=settings, provider=provider or FakeProvider(), auth_client=auth_client or FakeAuthClient())
    app.dependency_overrides[_db] = lambda: app.state.db
    app.dependency_overrides[_settings] = lambda: app.state.settings
    app.dependency_overrides[_provider] = lambda: app.state.provider
    app.dependency_overrides[_auth_client] = lambda: app.state.auth_client
    return app


def make_client(
    tmp_path: Path,
    auth_client: FakeAuthClient | None = None,
    provider: FakeProvider | None = None,
) -> TestClient:
    return TestClient(make_app(tmp_path, auth_client=auth_client, provider=provider))


def login_demo_user(client: TestClient) -> dict[str, Any]:
    response = client.post("/api/auth/login", json={"email": "demo@example.com", "password": "secret123"})
    assert response.status_code == 200
    return response.json()


def wait_for_task(client: TestClient, task_id: str, attempts: int = 60, delay: float = 0.02) -> dict[str, Any]:
    for _ in range(attempts):
        response = client.get(f"/api/tasks/{task_id}")
        assert response.status_code == 200
        task = response.json()
        if task["status"] in {"succeeded", "failed"}:
            return task
        time.sleep(delay)
    raise AssertionError(f"Task {task_id} did not finish in time")


def test_guest_config_masks_api_key(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        response = client.put("/api/config", json={"api_key": "sk-test-123456", "user_name": "Neo"})
        assert response.status_code == 200
        data = response.json()
        assert data["api_key_set"] is True
        assert data["api_key_hint"] == "sk-tes...3456"
        assert data["user_name"] == "Neo"
        assert data["managed_by_auth"] is False


def test_prompt_optimizer_uses_current_provider_key(tmp_path: Path) -> None:
    provider = FakeProvider()
    with make_client(tmp_path, provider=provider) as client:
        client.put("/api/config", json={"api_key": "sk-test-123456"})

        response = client.post(
            "/api/prompts/optimize",
            json={
                "prompt": "淘宝机器人主页图",
                "instruction": "主角改成白色家用机器人，保留电商主图质感",
                "size": "1088x1088",
                "aspect_ratio": "1:1",
                "quality": "medium",
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["prompt"] == "优化后的淘宝机器人主页图提示词"
        assert payload["model"] == "gpt-5.5"
        assert provider.chat_configs[-1]["api_key"] == "sk-test-123456"
        chat_payload = provider.chat_payloads[-1]
        assert chat_payload["model"] == "gpt-5.5"
        assert chat_payload["messages"][0]["role"] == "system"
        assert "白色家用机器人" in chat_payload["messages"][1]["content"]
        assert "比例 1:1" in chat_payload["messages"][1]["content"]


def test_prompt_optimizer_requires_api_key(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        response = client.post("/api/prompts/optimize", json={"prompt": "测试提示词"})

        assert response.status_code == 400
        assert "API Key" in response.json()["detail"]


def test_ecommerce_publish_copy_uses_current_provider_key(tmp_path: Path) -> None:
    provider = FakeProvider()
    with make_client(tmp_path, provider=provider) as client:
        client.put("/api/config", json={"api_key": "sk-test-123456"})

        response = client.post(
            "/api/ecommerce/publish-copy",
            json={
                "product_name": "天鹅绒PP棉抱枕芯",
                "materials": "天鹅绒填充，高密度磨毛布料",
                "selling_points": "可定做尺寸，蓬松回弹",
                "scenarios": "沙发、床头、办公休憩",
                "platform": "小红书",
                "style": "奶油风电商详情页",
                "image_count": 4,
                "size": "1152x2048",
                "aspect_ratio": "9:16",
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["title"] == "抱枕芯详情页种草图"
        assert "天鹅绒PP棉抱枕芯" in payload["body"]
        assert payload["model"] == "gpt-5.5"
        assert provider.chat_configs[-1]["api_key"] == "sk-test-123456"
        chat_payload = provider.chat_payloads[-1]
        assert "电商种草文案策划" in chat_payload["messages"][0]["content"]
        assert "可定做尺寸" in chat_payload["messages"][1]["content"]


def test_guest_generation_requires_login(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        client.put("/api/config", json={"api_key": "sk-test-123456"})

        response = client.post("/api/images/generate", json={"prompt": "neon city"})

        assert response.status_code == 401


def test_signed_in_history_is_isolated_by_cookie(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    with TestClient(app) as client_a, TestClient(app) as client_b:
        login_demo_user(client_a)
        generated = client_a.post("/api/images/generate", json={"prompt": "neon city"})
        assert generated.status_code == 200
        task = wait_for_task(client_a, generated.json()["id"])
        assert task["status"] == "succeeded"
        tasks = client_a.get("/api/tasks").json()["items"]

        history_a = client_a.get("/api/history").json()["items"]
        history_b = client_b.get("/api/history").json()["items"]
        config_b = client_b.get("/api/config").json()
        succeeded_tasks = client_a.get("/api/tasks?status=succeeded").json()["items"]
        queued_tasks = client_a.get("/api/tasks?status=queued").json()["items"]

        assert tasks[0]["id"] == generated.json()["id"]
        assert succeeded_tasks[0]["id"] == generated.json()["id"]
        assert queued_tasks == []
        assert len(history_a) == 1
        assert history_a[0]["prompt"] == "neon city"
        assert history_b == []
        assert config_b["api_key_set"] is False


def test_generation_passes_resolution_ratio_and_quality(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    with TestClient(app) as client:
        login_demo_user(client)

        generated = client.post(
            "/api/images/generate",
            json={"prompt": "wide city", "size": "2K", "aspect_ratio": "16:9", "quality": "high"},
        )

        assert generated.status_code == 200
        task = wait_for_task(client, generated.json()["id"])
        item = task["items"][0]
        provider = client.app.state.provider
        assert task["size"] == "2560x1440"
        assert task["aspect_ratio"] == "16:9"
        assert item["aspect_ratio"] == "16:9"
        assert provider.generated_payloads[-1]["size"] == "2560x1440"
        assert "aspectRatio" not in provider.generated_payloads[-1]
        assert provider.generated_payloads[-1]["quality"] == "high"


def test_generation_fans_out_multi_image_requests_into_one_task(tmp_path: Path) -> None:
    provider = FakeProvider()
    with make_client(tmp_path, provider=provider) as client:
        login_demo_user(client)

        generated = client.post(
            "/api/images/generate",
            json={"prompt": "nine product cards", "size": "1K", "aspect_ratio": "1:1", "quality": "low", "n": 3},
        )

        assert generated.status_code == 200
        task = wait_for_task(client, generated.json()["id"], attempts=120)
        history = client.get("/api/history").json()["items"]
        ledger = client.get("/api/ledger").json()["items"]

        assert task["status"] == "succeeded"
        assert task["result"]["count_requested"] == 3
        assert task["result"]["count_succeeded"] == 3
        assert task["result"]["series_plan"]["source"] == "planner"
        assert len(task["items"]) == 3
        assert len(provider.generated_payloads) == 3
        assert all(payload["n"] == 1 for payload in provider.generated_payloads)
        assert [payload["prompt"] for payload in provider.generated_payloads] == [
            "系列拆解提示词 generate 第1屏，统一风格，内容模块不同",
            "系列拆解提示词 generate 第2屏，统一风格，内容模块不同",
            "系列拆解提示词 generate 第3屏，统一风格，内容模块不同",
        ]
        assert [item["task_id"] for item in task["items"]] == [task["id"], task["id"], task["id"]]
        assert [item["batch_index"] for item in task["items"]] == [0, 1, 2]
        assert [item["prompt"] for item in task["items"]] == [
            "系列拆解提示词 generate 第1屏，统一风格，内容模块不同",
            "系列拆解提示词 generate 第2屏，统一风格，内容模块不同",
            "系列拆解提示词 generate 第3屏，统一风格，内容模块不同",
        ]
        assert len(history) == 3
        assert {item["task_id"] for item in history} == {task["id"]}
        assert {item["task_prompt"] for item in history} == {"nine product cards"}
        assert all(item["task_result"]["series_plan"]["source"] == "planner" for item in history)
        assert len(ledger) == 3


def test_generation_retries_retryable_upstream_errors(tmp_path: Path) -> None:
    provider = FlakyProvider(generate_failures=2)
    with make_client(tmp_path, provider=provider) as client:
        login_demo_user(client)

        generated = client.post("/api/images/generate", json={"prompt": "retryable cup"})

        assert generated.status_code == 200
        task = wait_for_task(client, generated.json()["id"], attempts=120)
        assert task["status"] == "succeeded"
        assert provider.generate_attempts == 3
        assert task["items"][0]["status"] == "succeeded"


def test_generation_records_nonzero_ledger_amount(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    with TestClient(app) as client:
        login_demo_user(client)
        client.put("/api/config", json={"api_key": "sk-shared-bonus-654321"})

        generated = client.post(
            "/api/images/generate",
            json={"prompt": "tall city", "size": "4K", "aspect_ratio": "9:16", "quality": "medium"},
        )

        assert generated.status_code == 200
        task = wait_for_task(client, generated.json()["id"])
        ledger = client.get("/api/ledger").json()["items"]
        assert task["size"] == "2160x3840"
        assert ledger[0]["history_id"] == task["items"][0]["id"]
        assert ledger[0]["amount"] == 0.268
        assert ledger[0]["metadata"]["size_tier"] == "4K"
        assert ledger[0]["metadata"]["usage"] == {"total_tokens": 1}


def test_managed_user_ledger_uses_sub2api_actual_cost(tmp_path: Path) -> None:
    auth = FakeAuthClient()
    auth.usage_logs[0]["actual_cost"] = 0.321
    with make_client(tmp_path, auth_client=auth) as client:
        login = client.post("/api/auth/login", json={"email": "demo@example.com", "password": "secret123"})
        assert login.status_code == 200

        generated = client.post(
            "/api/images/generate",
            json={"prompt": "managed billing", "size": "2K", "aspect_ratio": "1:1", "quality": "medium"},
        )

        assert generated.status_code == 200
        wait_for_task(client, generated.json()["id"])
        ledger = client.get("/api/ledger").json()["items"]
        assert ledger[0]["amount"] == 0.321
        assert ledger[0]["metadata"]["cost_source"] == "sub2api_actual_cost"
        assert ledger[0]["metadata"]["sub2api_usage_log"]["actual_cost"] == 0.321


def test_manual_override_ledger_keeps_estimated_cost(tmp_path: Path) -> None:
    auth = FakeAuthClient()
    with make_client(tmp_path, auth_client=auth) as client:
        login = client.post("/api/auth/login", json={"email": "demo@example.com", "password": "secret123"})
        assert login.status_code == 200
        overridden = client.put("/api/config", json={"api_key": "sk-shared-bonus-654321"})
        assert overridden.json()["api_key_source"] == "manual_override"

        generated = client.post(
            "/api/images/generate",
            json={"prompt": "shared key billing", "size": "2K", "aspect_ratio": "1:1", "quality": "medium"},
        )

        assert generated.status_code == 200
        wait_for_task(client, generated.json()["id"])
        ledger = client.get("/api/ledger").json()["items"]
        assert ledger[0]["amount"] == 0.201
        assert ledger[0]["metadata"]["cost_source"] == "local_image_price"


def test_image_size_presets_follow_provider_limits() -> None:
    assert _provider_image_size("1K", "1:1") == "1088x1088"
    assert _provider_image_size("1K", "16:9") == "2048x1152"
    assert _provider_image_size("1K", "9:16") == "1152x2048"
    assert _provider_image_size("1K", "3:2") == "1632x1088"
    assert _provider_image_size("2K", "1:1") == "1440x1440"
    assert _provider_image_size("2K", "16:9") == "2560x1440"
    assert _provider_image_size("2K", "3:2") == "2160x1440"
    assert _provider_image_size("4K", "16:9") == "3840x2160"
    with pytest.raises(HTTPException):
        _provider_image_size("576x1024", "9:16")
    with pytest.raises(HTTPException):
        _provider_image_size("1080x1920", "9:16")
    with pytest.raises(HTTPException):
        _provider_image_size("4K", "1:1")
    with pytest.raises(HTTPException):
        _provider_image_size("3840x3840", "1:1")
    with pytest.raises(HTTPException):
        _provider_image_size("4096x4096", "1:1")
    assert _image_size_tier("1088x1088") == "1K"
    assert _image_size_tier("2048x1152") == "1K"
    assert _image_size_tier("1440x1440") == "2K"
    assert _image_size_tier("2560x1440") == "2K"
    assert _image_size_tier("2160x3840") == "4K"


def test_edit_persists_upload_and_result(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        login_demo_user(client)

        response = client.post(
            "/api/images/edit",
            data={"prompt": "make it cyberpunk"},
            files=[
                ("image", ("source.png", b"fake-image", "image/png")),
                ("image", ("style.png", b"fake-style", "image/png")),
            ],
        )

        assert response.status_code == 200
        task = wait_for_task(client, response.json()["id"])
        item = task["items"][0]
        provider = client.app.state.provider
        assert item["mode"] == "edit"
        assert item["input_image_url"].startswith("/storage/uploads/")
        assert Path(item["input_image_path"]).exists()
        assert len(provider.edited_images[-1]) == 2
        assert provider.edited_images[-1][0][0] == "source.png"
        assert provider.edited_images[-1][1][0] == "style.png"


def test_edit_reference_notes_are_sent_to_provider_and_history(tmp_path: Path) -> None:
    provider = FakeProvider()
    with make_client(tmp_path, provider=provider) as client:
        login_demo_user(client)

        response = client.post(
            "/api/images/edit",
            data={
                "prompt": "根据多角度参考图生成商品图",
                "reference_notes": json.dumps(
                    [
                        {"index": 0, "role": "正面", "note": "保持正面轮廓和颜色"},
                        {"index": 1, "role": "材质细节", "note": "只参考绒面质感"},
                    ],
                    ensure_ascii=False,
                ),
            },
            files=[
                ("image", ("front.png", b"fake-front", "image/png")),
                ("image", ("material.png", b"fake-material", "image/png")),
            ],
        )

        assert response.status_code == 200
        task = wait_for_task(client, response.json()["id"])
        item = task["items"][0]
        prompt = provider.edited_fields[-1]["prompt"]
        assert "参考图说明" in prompt
        assert "图1：正面，保持正面轮廓和颜色" in prompt
        assert "图2：材质细节，只参考绒面质感" in prompt
        assert item["task_request"]["reference_notes"][0]["role"] == "正面"
        assert item["task_request"]["reference_notes"][1]["note"] == "只参考绒面质感"


def test_edit_fans_out_multi_image_requests_with_series_prompts(tmp_path: Path) -> None:
    provider = FakeProvider()
    with make_client(tmp_path, provider=provider) as client:
        login_demo_user(client)

        response = client.post(
            "/api/images/edit",
            data={"prompt": "根据产品图生成四屏详情页", "n": "2", "size": "1K", "aspect_ratio": "9:16"},
            files=[
                ("image", ("product.png", b"fake-product", "image/png")),
            ],
        )

        assert response.status_code == 200
        task = wait_for_task(client, response.json()["id"], attempts=120)
        assert task["status"] == "succeeded"
        assert task["result"]["series_plan"]["source"] == "planner"
        assert len(task["items"]) == 2
        assert len(provider.edited_fields) == 2
        assert all(fields["n"] == 1 for fields in provider.edited_fields)
        assert [fields["prompt"] for fields in provider.edited_fields] == [
            "系列拆解提示词 edit 第1屏，统一风格，内容模块不同",
            "系列拆解提示词 edit 第2屏，统一风格，内容模块不同",
        ]
        assert [len(images) for images in provider.edited_images] == [1, 1]
        assert [item["input_image_url"] for item in task["items"]]


def test_ecommerce_generate_analyzes_product_and_creates_series_edit_task(tmp_path: Path) -> None:
    provider = FakeProvider()
    with make_client(tmp_path, provider=provider) as client:
        login_demo_user(client)

        response = client.post(
            "/api/ecommerce/generate",
            data={
                "product_name": "天鹅绒PP棉抱枕芯",
                "materials": "天鹅绒填充，高密度磨毛布料",
                "selling_points": "可定做尺寸，蓬松回弹",
                "scenarios": "沙发、床头、办公休憩",
                "platform": "淘宝",
                "style": "奶油风电商详情页",
                "n": "2",
                "size": "1K",
                "aspect_ratio": "9:16",
            },
            files={"image": ("product.png", b"fake-product", "image/png")},
        )

        assert response.status_code == 200
        task = wait_for_task(client, response.json()["id"], attempts=120)
        assert task["status"] == "succeeded"
        assert task["mode"] == "edit"
        assert task["result"]["ecommerce_analysis"]["source"] == "vision"
        assert task["result"]["ecommerce_analysis"]["product_type"] == "抱枕芯"
        assert task["result"]["series_plan"]["source"] == "planner"
        assert len(task["items"]) == 2
        assert len(provider.edited_fields) == 2
        assert all(fields["size"] == "1152x2048" for fields in provider.edited_fields)
        assert all(fields["n"] == 1 for fields in provider.edited_fields)
        assert all("商品一致性强约束" in fields["prompt"] for fields in provider.edited_fields)
        assert all("保持白色方形抱枕芯主体" in fields["prompt"] for fields in provider.edited_fields)
        assert [item["input_image_url"] for item in task["items"]]
        history = client.get("/api/history").json()["items"]
        assert history[0]["task_request"]["ecommerce"]["product_name"] == "天鹅绒PP棉抱枕芯"
        planner_messages = [
            payload["messages"][1]["content"]
            for payload in provider.chat_payloads
            if "系列图像提示词规划师" in payload["messages"][0]["content"]
        ]
        assert planner_messages and "商品图识别结果" in planner_messages[-1]


def test_ecommerce_generate_analyzes_all_reference_angles(tmp_path: Path) -> None:
    provider = FakeProvider()
    with make_client(tmp_path, provider=provider) as client:
        login_demo_user(client)

        response = client.post(
            "/api/ecommerce/generate",
            data={
                "product_name": "青花汾20",
                "reference_notes": json.dumps(
                    [
                        {"index": 0, "role": "正面", "primary": True},
                        {"index": 1, "role": "侧面"},
                    ],
                    ensure_ascii=False,
                ),
                "n": "2",
                "size": "1K",
                "aspect_ratio": "1:1",
            },
            files=[
                ("image", ("front.jpg", b"fake-front", "image/jpeg")),
                ("reference_image", ("side.jpg", b"fake-side", "image/jpeg")),
            ],
        )

        assert response.status_code == 200
        task = wait_for_task(client, response.json()["id"], attempts=120)
        assert task["status"] == "succeeded"
        analyzer_payload = next(
            payload
            for payload in provider.chat_payloads
            if "电商商品图识别分析师" in payload["messages"][0]["content"]
        )
        analyzer_content = analyzer_payload["messages"][1]["content"]
        assert sum(1 for item in analyzer_content if item.get("type") == "image_url") == 2
        analyzer_text = analyzer_content[0]["text"]
        assert "图1：正面" in analyzer_text
        assert "图2：侧面" in analyzer_text
        planner_messages = [
            payload["messages"][1]["content"]
            for payload in provider.chat_payloads
            if "系列图像提示词规划师" in payload["messages"][0]["content"]
        ]
        assert "图2：侧面" in planner_messages[-1]
        assert all("图2：侧面" in fields["prompt"] for fields in provider.edited_fields)
        assert len(provider.edited_images[-1]) == 2
        assert task["items"][0]["task_request"]["reference_notes"][1]["role"] == "侧面"


def test_history_item_can_be_used_as_single_edit_source(tmp_path: Path) -> None:
    provider = FakeProvider()
    with make_client(tmp_path, provider=provider) as client:
        login_demo_user(client)
        generated = client.post("/api/images/generate", json={"prompt": "original product", "size": "1K", "aspect_ratio": "1:1"})
        source_task = wait_for_task(client, generated.json()["id"])
        source_item = source_task["items"][0]

        response = client.post(
            f"/api/history/{source_item['id']}/edit",
            json={"prompt": "基于这张图改成夏季风格", "size": "1K", "aspect_ratio": "1:1", "quality": "medium"},
        )

        assert response.status_code == 200
        edit_task = wait_for_task(client, response.json()["id"])
        assert edit_task["status"] == "succeeded"
        assert edit_task["mode"] == "edit"
        assert edit_task["prompt"] == "基于这张图改成夏季风格"
        assert provider.edited_fields[-1]["prompt"].startswith("基于这张图改成夏季风格")
        assert "第一张图是当前成品图" in provider.edited_fields[-1]["prompt"]
        assert len(provider.edited_images[-1]) == 1
        assert edit_task["items"][0]["input_image_url"] == source_item["image_url"]


def test_ecommerce_history_edit_uses_product_current_and_extra_references(tmp_path: Path) -> None:
    provider = FakeProvider()
    with make_client(tmp_path, provider=provider) as client:
        login_demo_user(client)

        response = client.post(
            "/api/ecommerce/generate",
            data={
                "product_name": "天鹅绒PP棉抱枕芯",
                "materials": "天鹅绒填充",
                "n": "1",
                "size": "1K",
                "aspect_ratio": "1:1",
            },
            files={"image": ("product.png", b"fake-product", "image/png")},
        )
        assert response.status_code == 200
        source_task = wait_for_task(client, response.json()["id"], attempts=120)
        source_item = source_task["items"][0]
        source_prompt = source_item["prompt"]
        history_before = client.get("/api/history").json()["items"]
        assert len(history_before) == 1

        edit_response = client.post(
            f"/api/history/{source_item['id']}/edit",
            data={"prompt": "把这一屏改成材质特写，保留商品主体", "size": "1K", "aspect_ratio": "1:1", "quality": "medium"},
            files={"image": ("scene.png", b"fake-scene", "image/png")},
        )

        assert edit_response.status_code == 200
        edit_task = wait_for_task(client, edit_response.json()["id"])
        assert edit_task["status"] == "succeeded"
        assert edit_task["prompt"] == "把这一屏改成材质特写，保留商品主体"
        assert [item["id"] for item in edit_task["items"]] == [source_item["id"]]
        assert edit_task["items"][0]["input_image_url"] == source_item["input_image_url"]
        assert edit_task["items"][0]["prompt"] == "把这一屏改成材质特写，保留商品主体"
        assert edit_task["items"][0]["task_id"] == source_item["task_id"]
        assert edit_task["items"][0]["task_request"]["ecommerce"]["product_name"] == "天鹅绒PP棉抱枕芯"
        assert edit_task["items"][0]["image_url"] != source_item["image_url"]
        assert edit_task["items"][0]["prompt"] != source_prompt
        history_after = client.get("/api/history").json()["items"]
        assert len(history_after) == 1
        assert history_after[0]["id"] == source_item["id"]
        assert provider.edited_fields[-1]["prompt"].startswith("把这一屏改成材质特写，保留商品主体")
        assert "第一张图是原商品主图" in provider.edited_fields[-1]["prompt"]
        assert "额外上传的 1 张参考图" in provider.edited_fields[-1]["prompt"]
        assert len(provider.edited_images[-1]) == 3
        assert provider.edited_images[-1][0][0].endswith(".png")
        assert provider.edited_images[-1][1][0].endswith(".png")
        assert provider.edited_images[-1][2][0] == "scene.png"
        ledger = client.get("/api/ledger").json()["items"]
        assert ledger[0]["history_id"] == source_item["id"]


def test_account_includes_balance_and_stats(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        login_demo_user(client)
        generated = client.post("/api/images/generate", json={"prompt": "one"})
        wait_for_task(client, generated.json()["id"])

        response = client.get("/api/account")

        assert response.status_code == 200
        data = response.json()
        assert data["balance"]["remaining"] == 12.5
        assert data["stats"]["total"] == 1
        assert data["viewer"]["authenticated"] is True


def test_user_can_publish_and_unpublish_history_as_public_case(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        login_demo_user(client)
        generated = client.post("/api/images/generate", json={"prompt": "public neon gallery"})
        task = wait_for_task(client, generated.json()["id"])
        history_id = task["items"][0]["id"]

        history_before = client.get("/api/history").json()["items"][0]
        assert history_before["published"] is False

        published = client.post(f"/api/history/{history_id}/publish")
        assert published.status_code == 200
        published_data = published.json()
        assert published_data["item"]["published"] is True
        assert published_data["inspiration"]["section"] == "用户作品"
        assert published_data["inspiration"]["prompt"] == "public neon gallery"

        public_cases_payload = client.get("/api/inspirations?q=public%20neon").json()
        public_cases = public_cases_payload["items"]
        assert public_cases_payload["total"] == 1
        assert len(public_cases) == 1
        assert public_cases[0]["source_url"] == "joko-image://user-gallery"

        unpublished = client.delete(f"/api/history/{history_id}/publish")
        assert unpublished.status_code == 200
        assert unpublished.json()["item"]["published"] is False
        public_cases_after_payload = client.get("/api/inspirations?q=public%20neon").json()
        public_cases_after = public_cases_after_payload["items"]
        assert public_cases_after_payload["total"] == 0
        assert public_cases_after == []


def test_signed_in_user_can_favorite_public_cases(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        db = client.app.state.db
        db.upsert_inspirations(
            "https://example.com/README.md",
            [
                {
                    "id": "case-fav-1",
                    "source_item_id": "case-fav-1",
                    "section": "Gallery",
                    "title": "Favorite Demo",
                    "author": "@demo",
                    "prompt": "favorite prompt",
                    "image_url": "https://example.com/favorite.jpg",
                    "source_link": "https://example.com/post",
                    "raw": {},
                }
            ],
        )

        guest_cases = client.get("/api/inspirations?q=favorite").json()["items"]
        assert guest_cases[0]["favorited"] is False
        assert client.post("/api/inspirations/case-fav-1/favorite").status_code == 401

        login = client.post("/api/auth/login", json={"email": "demo@example.com", "password": "secret123"})
        assert login.status_code == 200

        before = client.get("/api/inspirations?q=favorite").json()["items"][0]
        assert before["favorited"] is False

        favorited = client.post("/api/inspirations/case-fav-1/favorite")
        assert favorited.status_code == 200
        assert favorited.json()["item"]["favorited"] is True

        after = client.get("/api/inspirations?q=favorite").json()["items"][0]
        favorites = client.get("/api/inspirations/favorites").json()
        assert after["favorited"] is True
        assert favorites["total"] == 1
        assert favorites["items"][0]["id"] == "case-fav-1"

        unfavorited = client.delete("/api/inspirations/case-fav-1/favorite")
        assert unfavorited.status_code == 200
        assert unfavorited.json()["item"]["favorited"] is False
        assert client.get("/api/inspirations/favorites").json()["total"] == 0


def test_ai_inspiration_search_keeps_keyword_search_separate(tmp_path: Path) -> None:
    provider = FakeProvider()
    with make_client(tmp_path, provider=provider) as client:
        client.put("/api/config", json={"api_key": "sk-test-123456"})
        db = client.app.state.db
        db.upsert_inspirations(
            "https://example.com/README.md",
            [
                {
                    "id": "case-ai-1",
                    "source_item_id": "case-ai-1",
                    "section": "Gallery",
                    "title": "赛博城市夜景",
                    "author": "@demo",
                    "prompt": "赛博 城市 夜景 霓虹雨夜",
                    "image_url": "https://example.com/cyber.jpg",
                    "source_link": "https://example.com/post",
                    "raw": {},
                }
            ],
        )

        response = client.post("/api/inspirations/ai-search", json={"query": "找一些未来感城市夜景"})

        assert response.status_code == 200
        data = response.json()
        assert data["query"] == "赛博 城市 夜景"
        assert data["total"] == 1
        assert data["items"][0]["id"] == "case-ai-1"
        assert provider.chat_payloads[-1]["messages"][0]["content"].startswith("你是 JokoAI 的案例库搜索助手")


def test_login_binds_managed_key_with_default_group(tmp_path: Path) -> None:
    auth = FakeAuthClient()
    with make_client(tmp_path, auth_client=auth) as client:
        login = client.post("/api/auth/login", json={"email": "demo@example.com", "password": "secret123"})
        assert login.status_code == 200
        assert login.json()["viewer"]["authenticated"] is True
        assert auth.created_keys and auth.created_keys[0]["name"] == "cybergen-image"
        assert auth.created_keys[0]["group"]["id"] == 12

        config = client.get("/api/config").json()
        history = client.get("/api/history").json()["items"]
        account = client.get("/api/account").json()

        assert config["managed_by_auth"] is True
        assert config["api_key_hint"] == "sk-use...3456"
        assert len(history) == 0
        assert account["viewer"]["user"]["email"] == "demo@example.com"
        assert account["user"]["api_key_source"] == "managed"
        assert account["viewer"]["user"]["role"] == "admin"


def test_register_creates_unlimited_trial_key_and_records_balance_grant(tmp_path: Path) -> None:
    auth = FakeAuthClient()
    with make_client(tmp_path, auth_client=auth) as client:
        register = client.post("/api/auth/register", json={"email": "new@example.com", "password": "secret123"})

        assert register.status_code == 200
        assert auth.created_keys and auth.created_keys[0]["name"].startswith("joko-image2-trial")
        assert auth.created_keys[0]["quota"] == 0
        assert auth.created_keys[0]["expires_in_days"] == 30
        assert auth.created_keys[0]["group"]["id"] == 12
        assert auth.admin_balance_calls == []

        grant = client.app.state.db.get_trial_grant(owner_id="user:7")
        assert grant["status"] == "partial"
        assert grant["quota_usd"] == 0
        assert grant["balance_granted_usd"] == 0
        assert "SUB2API_ADMIN_TOKEN" in grant["error"]

        config = client.get("/api/config").json()
        assert config["api_key_source"] == "managed"
        assert config["api_key_hint"] == "sk-use...3456"


def test_register_grants_trial_balance_when_admin_token_configured(tmp_path: Path) -> None:
    auth = FakeAuthClient()
    app = make_app(tmp_path, auth_client=auth)
    app.state.settings = app.state.settings.__class__(
        **{
            **app.state.settings.__dict__,
            "sub2api_admin_token": "admin-local-token",
        }
    )
    with TestClient(app) as client:
        register = client.post("/api/auth/register", json={"email": "new@example.com", "password": "secret123"})

        assert register.status_code == 200
        assert len(auth.created_keys) == 1
        assert auth.admin_balance_calls == [
            {
                "base_url": "http://127.0.0.1:9878",
                "admin_token": "admin-local-token",
                "user_id": 7,
                "payload": {
                    "balance": 2,
                    "operation": "add",
                    "notes": "joko-image2 new user trial grant",
                },
                "token_type": "api_key",
            }
        ]
        grant = app.state.db.get_trial_grant(owner_id="user:7")
        assert grant["status"] == "created"
        assert grant["balance_granted_usd"] == 2
        assert grant["error"] is None


def test_admin_can_configure_sub2api_admin_token_in_site_settings(tmp_path: Path) -> None:
    auth = FakeAuthClient()
    app = make_app(tmp_path, auth_client=auth)
    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"email": "demo@example.com", "password": "secret123"})
        assert login.status_code == 200

        saved = client.put(
            "/api/site-settings",
            json={
                "sub2api_admin_token": "admin-token-from-ui",
                "sub2api_admin_jwt": "jwt-token-from-ui",
            },
        )

        assert saved.status_code == 200
        upstream = saved.json()["upstream"]
        assert upstream["sub2api_admin_token_set"] is True
        assert upstream["sub2api_admin_token_hint"] == "admin-...m-ui"
        assert upstream["sub2api_admin_jwt_set"] is True
        assert upstream["sub2api_admin_jwt_hint"] == "jwt-to...m-ui"

        kept = client.put("/api/site-settings", json={"sub2api_admin_token": "", "sub2api_admin_jwt": ""})
        kept_upstream = kept.json()["upstream"]
        assert kept_upstream["sub2api_admin_token_set"] is True
        assert kept_upstream["sub2api_admin_token_hint"] == "admin-...m-ui"
        assert kept_upstream["sub2api_admin_jwt_set"] is True
        assert kept_upstream["sub2api_admin_jwt_hint"] == "jwt-to...m-ui"


def test_register_uses_site_configured_sub2api_admin_token(tmp_path: Path) -> None:
    auth = FakeAuthClient()
    app = make_app(tmp_path, auth_client=auth)
    app.state.db.update_site_settings({"sub2api_admin_token": "admin-token-from-db"})
    with TestClient(app) as client:
        register = client.post("/api/auth/register", json={"email": "new@example.com", "password": "secret123"})

        assert register.status_code == 200
        assert auth.admin_balance_calls[0]["admin_token"] == "admin-token-from-db"
        assert auth.admin_balance_calls[0]["token_type"] == "api_key"
        grant = app.state.db.get_trial_grant(owner_id="user:7")
        assert grant["status"] == "created"
        assert grant["balance_granted_usd"] == 2


def test_admin_can_configure_trial_balance_amount(tmp_path: Path) -> None:
    auth = FakeAuthClient()
    app = make_app(tmp_path, auth_client=auth)
    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"email": "demo@example.com", "password": "secret123"})
        assert login.status_code == 200

        saved = client.put("/api/site-settings", json={"trial_balance_usd": 5.5})

        assert saved.status_code == 200
        upstream = saved.json()["upstream"]
        assert upstream["trial_balance_usd"] == 5.5
        assert upstream["configured_trial_balance_usd"] == 5.5


def test_register_uses_site_configured_trial_balance_amount(tmp_path: Path) -> None:
    auth = FakeAuthClient()
    app = make_app(tmp_path, auth_client=auth)
    app.state.db.update_site_settings({"sub2api_admin_token": "admin-token-from-db", "trial_balance_usd": 3.25})
    with TestClient(app) as client:
        register = client.post("/api/auth/register", json={"email": "new@example.com", "password": "secret123"})

        assert register.status_code == 200
        assert auth.admin_balance_calls[0]["payload"]["balance"] == 3.25
        grant = app.state.db.get_trial_grant(owner_id="user:7")
        assert grant["status"] == "created"
        assert grant["balance_granted_usd"] == 3.25


def test_login_does_not_create_trial_key(tmp_path: Path) -> None:
    auth = FakeAuthClient()
    with make_client(tmp_path, auth_client=auth) as client:
        login = client.post("/api/auth/login", json={"email": "demo@example.com", "password": "secret123"})

        assert login.status_code == 200
        assert auth.created_keys and auth.created_keys[0]["name"] == "cybergen-image"
        assert auth.created_keys[0]["quota"] == 0
        assert auth.admin_balance_calls == []
        assert client.app.state.db.get_trial_grant(owner_id="user:7") is None


def test_signed_in_user_can_override_key_and_clear_back_to_managed(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        login = client.post("/api/auth/login", json={"email": "demo@example.com", "password": "secret123"})
        assert login.status_code == 200

        overridden = client.put("/api/config", json={"api_key": "sk-shared-bonus-654321"})
        assert overridden.status_code == 200
        overridden_data = overridden.json()
        assert overridden_data["api_key_hint"] == "sk-sha...4321"
        assert overridden_data["api_key_source"] == "manual_override"

        account = client.get("/api/account").json()
        assert account["user"]["api_key_source"] == "manual_override"

        restored = client.put("/api/config", json={"clear_api_key": True})
        assert restored.status_code == 200
        restored_data = restored.json()
        assert restored_data["api_key_hint"] == "sk-use...3456"
        assert restored_data["api_key_source"] == "managed"


def test_site_settings_default_to_chinese(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        response = client.get("/api/site-settings")

        assert response.status_code == 200
        data = response.json()
        assert data["default_locale"] == "zh-CN"
        assert data["announcement"]["enabled"] is True
        assert "JokoAI" in data["announcement"]["title"]
        assert "https://ai.get-money.locker" in data["announcement"]["body"]
        assert data["inspiration_sources"] == ["https://example.com/README.md"]
        assert data["recharge_url"] == "https://ai.get-money.locker"


def test_admin_can_update_site_settings(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        login = client.post("/api/auth/login", json={"email": "demo@example.com", "password": "secret123"})
        assert login.status_code == 200

        response = client.put(
            "/api/site-settings",
            json={
                "default_locale": "en-US",
                "announcement_enabled": True,
                "announcement_title": "系统维护通知",
                "announcement_body": "今晚 23:00 会进行维护。",
                "inspiration_sources": [
                    "https://github.com/YouMind-OpenLab/awesome-gpt-image-2",
                    "https://raw.githubusercontent.com/EvoLinkAI/awesome-gpt-image-2-prompts/main/README.md",
                ],
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["default_locale"] == "en-US"
        assert data["announcement"]["enabled"] is True
        assert data["announcement"]["title"] == "系统维护通知"
        assert data["inspiration_sources"][0] == "https://raw.githubusercontent.com/YouMind-OpenLab/awesome-gpt-image-2/main/README.md"


def test_admin_can_update_global_upstream_settings(tmp_path: Path) -> None:
    auth = FakeAuthClient()
    app = make_app(tmp_path, auth_client=auth)
    provider = app.state.provider
    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"email": "demo@example.com", "password": "secret123"})
        assert login.status_code == 200

        response = client.put(
            "/api/site-settings",
            json={
                "provider_base_url": "https://image-upstream.example.com/v1/",
                "auth_base_url": "https://auth-upstream.example.com/",
                "recharge_url": "https://pay.example.com/recharge/",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["upstream"]["provider_base_url"] == "https://image-upstream.example.com/v1"
        assert data["upstream"]["auth_base_url"] == "https://auth-upstream.example.com"
        assert data["upstream"]["effective_provider_base_url"] == "https://image-upstream.example.com/v1"
        assert data["upstream"]["effective_auth_base_url"] == "https://auth-upstream.example.com"
        assert data["upstream"]["recharge_url"] == "https://pay.example.com/recharge"
        assert data["upstream"]["effective_recharge_url"] == "https://pay.example.com/recharge"

        public_settings = client.get("/api/auth/public-settings")
        assert public_settings.status_code == 200
        assert auth.public_settings_base_urls[-1] == "https://auth-upstream.example.com"

        generated = client.post(
            "/api/images/generate",
            json={"prompt": "custom upstream", "size": "2K", "aspect_ratio": "1:1", "quality": "medium"},
        )
        assert generated.status_code == 200
        wait_for_task(client, generated.json()["id"])

        assert provider.generated_configs[-1]["base_url"] == "https://image-upstream.example.com/v1"
        assert auth.list_usage_base_urls[-1] == "https://auth-upstream.example.com"


def test_invalid_global_upstream_url_is_rejected(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        login = client.post("/api/auth/login", json={"email": "demo@example.com", "password": "secret123"})
        assert login.status_code == 200

        response = client.put("/api/site-settings", json={"provider_base_url": "not-a-url"})

        assert response.status_code == 400


def test_payment_proxy_requires_login(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        response = client.get("/api/payment/checkout-info")

        assert response.status_code == 401


def test_payment_proxy_uses_logged_in_access_token(tmp_path: Path) -> None:
    auth = FakeAuthClient()
    app = make_app(tmp_path, auth_client=auth)
    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"email": "demo@example.com", "password": "secret123"})
        assert login.status_code == 200

        checkout = client.get("/api/payment/checkout-info")
        assert checkout.status_code == 200
        assert checkout.json()["methods"]["alipay"]["available"] is True

        order = client.post(
            "/api/payment/orders",
            json={"amount": 2, "payment_type": "alipay", "order_type": "balance"},
        )
        assert order.status_code == 200
        assert order.json()["order_id"] == 88

        detail = client.get("/api/payment/orders/88")
        assert detail.status_code == 200
        assert detail.json()["status"] == "COMPLETED"

        verify = client.post("/api/payment/orders/verify", json={"out_trade_no": "sub2_demo"})
        assert verify.status_code == 200

        calls = auth.payment_calls
        assert [call["method"] for call in calls] == ["checkout_info", "create_order", "get_order", "verify_order"]
        assert all(call["base_url"] == "http://127.0.0.1:9878" for call in calls)
        assert all(call["access_token"] == "access-demo" for call in calls)
        assert calls[1]["payload"] == {"amount": 2.0, "payment_type": "alipay", "order_type": "balance"}


def test_parse_inspiration_markdown() -> None:
    markdown = """
## Portrait & Photography Cases

### Case 1: [Convenience Store Neon Portrait](https://x.com/demo/status/1) (by [@demo](https://x.com/demo))

| Output |
| :----: |
| <img src="./images/portrait_case1/output.jpg" width="300" alt="Output image"> |

**Prompt:**

```
35mm film photography, neon signs, authentic grain
```
"""
    items = parse_inspiration_markdown(
        markdown,
        "https://raw.githubusercontent.com/EvoLinkAI/awesome-gpt-image-2-prompts/main/README.md",
    )

    assert len(items) == 1
    assert items[0]["section"] == "Portrait & Photography Cases"
    assert items[0]["title"] == "Convenience Store Neon Portrait"
    assert items[0]["author"] == "@demo"
    assert items[0]["source_link"] == "https://x.com/demo/status/1"
    assert items[0]["image_url"].endswith("/images/portrait_case1/output.jpg")
    assert "35mm film" in items[0]["prompt"]


def test_parse_youmind_inspiration_markdown() -> None:
    markdown = """
## 🔥 Featured Prompts

### No. 1: VR Headset Exploded View Poster

#### 📖 Description

Generates a high-tech exploded view diagram.

#### 📝 Prompt

```
{
  "type": "exploded view product diagram poster",
  "subject": "VR headset"
}
```

#### 🖼️ Generated Images

##### Image 1

<div align="center">
<img src="https://cms-assets.youmind.com/media/demo.jpg" width="700" alt="VR Headset Exploded View Poster - Image 1">
</div>

#### 📌 Details

- **Author:** [wory](https://x.com/wory37303852)
- **Source:** [Twitter Post](https://x.com/wory37303852/status/2045925660401795478)
"""
    items = parse_inspiration_markdown(
        markdown,
        "https://raw.githubusercontent.com/YouMind-OpenLab/awesome-gpt-image-2/main/README.md",
    )

    assert len(items) == 1
    assert items[0]["section"] == "🔥 Featured Prompts"
    assert items[0]["title"] == "VR Headset Exploded View Poster"
    assert items[0]["author"] == "wory"
    assert items[0]["source_link"] == "https://x.com/wory37303852/status/2045925660401795478"
    assert items[0]["image_url"] == "https://cms-assets.youmind.com/media/demo.jpg"
    assert "exploded view product" in items[0]["prompt"]


def test_normalize_github_inspiration_source_url() -> None:
    assert (
        normalize_inspiration_source_url("https://github.com/YouMind-OpenLab/awesome-gpt-image-2")
        == "https://raw.githubusercontent.com/YouMind-OpenLab/awesome-gpt-image-2/main/README.md"
    )
    assert (
        normalize_inspiration_source_url(
            "https://github.com/YouMind-OpenLab/awesome-gpt-image-2/blob/main/README_zh.md"
        )
        == "https://raw.githubusercontent.com/YouMind-OpenLab/awesome-gpt-image-2/main/README_zh.md"
    )


def test_cache_inspiration_images_to_local_storage(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    settings = app.state.settings
    items = [{"image_url": "https://cdn.example.com/case.png", "raw": {}}]

    async def run_cache() -> dict[str, Any]:
        def handler(request: httpx.Request) -> httpx.Response:
            assert str(request.url) == "https://cdn.example.com/case.png"
            return httpx.Response(200, headers={"content-type": "image/png"}, content=b"png-data")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await cache_inspiration_images(settings, client, items)

    result = asyncio.run(run_cache())

    assert result["cached"] == 1
    assert items[0]["image_url"].startswith("/storage/inspirations/")
    assert items[0]["raw"]["original_image_url"] == "https://cdn.example.com/case.png"
    cached_path = settings.storage_dir / items[0]["image_url"].removeprefix("/storage/")
    assert cached_path.read_bytes() == b"png-data"


def test_manual_inspiration_sync_endpoint(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        db = client.app.state.db
        db.upsert_inspirations(
            "https://example.com/README.md",
            [
                {
                    "id": "abc",
                    "source_item_id": "abc",
                    "section": "UI",
                    "title": "Mockup",
                    "author": "@demo",
                    "prompt": "make a UI",
                    "image_url": "https://example.com/image.jpg",
                    "source_link": "https://example.com/post",
                    "raw": {},
                }
            ],
        )

        response = client.get("/api/inspirations")

        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 1
        item = payload["items"][0]
        assert item["title"] == "Mockup"


def test_inspiration_sync_requires_admin(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        guest = client.post("/api/inspirations/sync")
        assert guest.status_code == 401

        login_demo_user(client)
        response = client.post("/api/inspirations/sync")

        assert response.status_code in {200, 502}
