from __future__ import annotations

from typing import Any

import httpx


class ProviderError(Exception):
    def __init__(self, status_code: int, message: str, payload: Any | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.payload = payload


class OpenAICompatibleImageClient:
    def __init__(self, timeout_seconds: float = 300):
        self.timeout = httpx.Timeout(timeout_seconds, connect=20)

    async def test_connection(self, config: dict[str, Any]) -> dict[str, Any]:
        response = await self._request(config, "GET", "/models")
        data = response.json()
        models = [item.get("id") for item in data.get("data", []) if isinstance(item, dict)]
        return {"ok": True, "models": models[:30], "raw": data}

    async def usage(self, config: dict[str, Any]) -> dict[str, Any]:
        usage_path = config.get("usage_path") or "/v1/usage"
        response = await self._request(config, "GET", usage_path, absolute_path=True)
        data = response.json()
        return {"ok": True, "remaining": _extract_remaining(data), "raw": data}

    async def generate_image(self, config: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        response = await self._request(config, "POST", "/images/generations", json=payload)
        return response.json()

    async def chat_completion(self, config: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        response = await self._request(config, "POST", "/chat/completions", json=payload)
        return response.json()

    async def edit_image(
        self,
        config: dict[str, Any],
        fields: dict[str, Any],
        images: list[tuple[str, bytes, str]],
        mask: tuple[str, bytes, str] | None = None,
    ) -> dict[str, Any]:
        files: list[tuple[str, tuple[str, bytes, str]]] = [
            ("image", (filename, content, content_type)) for filename, content, content_type in images
        ]
        if mask is not None:
            files.append(("mask", mask))
        response = await self._request(config, "POST", "/images/edits", data=fields, files=files)
        return response.json()

    async def _request(
        self,
        config: dict[str, Any],
        method: str,
        path: str,
        *,
        absolute_path: bool = False,
        **kwargs: Any,
    ) -> httpx.Response:
        api_key = (config.get("api_key") or "").strip()
        if not api_key:
            raise ProviderError(400, "请先在配置页保存 JokoAI API Key")

        url = _join_absolute_path(config["base_url"], path) if absolute_path else _join_base(config["base_url"], path)
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {api_key}"

        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            response = await client.request(method, url, headers=headers, **kwargs)

        if response.status_code >= 400:
            raise ProviderError(response.status_code, _extract_error_message(response), _safe_json(response))
        return response


def _join_base(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def _join_absolute_path(base_url: str, path: str) -> str:
    if not path.startswith("/"):
        return _join_base(base_url, path)
    parsed = httpx.URL(base_url)
    return str(parsed.copy_with(path=path, query=None))


def _safe_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text[:1000]


def _extract_error_message(response: httpx.Response) -> str:
    payload = _safe_json(response)
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
        if payload.get("message"):
            return str(payload["message"])
        if payload.get("error"):
            return str(payload["error"])
    return response.text[:1000] or f"Provider returned HTTP {response.status_code}"


def _extract_remaining(payload: Any) -> float | None:
    if not isinstance(payload, dict):
        return None
    for key in ("remaining", "balance"):
        value = payload.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    quota = payload.get("quota")
    if isinstance(quota, dict) and isinstance(quota.get("remaining"), (int, float)):
        return float(quota["remaining"])
    return None
