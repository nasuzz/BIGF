from __future__ import annotations

import logging
import os
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import quote, urlparse

import httpx


logger = logging.getLogger(__name__)

_DEFAULT_REQUEST_TIMEOUT_SEC = 50.0
_DEFAULT_HCX_TIMEOUT_SEC = 20.0
_REQUEST_BUDGET_RESERVE_SEC = 10.0
_DEFAULT_MAX_TOKENS = 800


class HcxError(RuntimeError):
    """Base class for credential-free HCX failures."""


class HcxConfigurationError(HcxError):
    pass


class HcxTimeoutError(HcxError):
    pass


class HcxAuthenticationError(HcxError):
    pass


class HcxServiceError(HcxError):
    pass


class HcxResponseError(HcxError):
    pass


class HttpResponse(Protocol):
    status_code: int

    def json(self) -> Any: ...


class HttpClient(Protocol):
    def post(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        json: Mapping[str, Any],
        timeout: float,
    ) -> HttpResponse: ...


@dataclass(frozen=True)
class HcxConfig:
    api_key: str
    endpoint: str
    model_name: str
    timeout_sec: float
    max_tokens: int


class HcxClient:
    """Minimal non-streaming CLOVA Studio Chat Completions v3 client."""

    def __init__(
        self,
        config: HcxConfig,
        http_client: HttpClient | None = None,
    ) -> None:
        self.config = config
        self._http_client = http_client or httpx.Client()
        self._url = _completion_url(config.endpoint, config.model_name)

    def generate(self, prompt: str) -> str:
        request_id = str(uuid.uuid4())
        token = self.config.api_key.removeprefix("Bearer ").strip()
        headers = {
            "Authorization": f"Bearer {token}",
            "X-NCP-CLOVASTUDIO-REQUEST-ID": request_id,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        body = {
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "당신은 금융상품 검색 결과를 문장으로 정리하는 "
                        "도우미입니다. 사용자 메시지의 검색 근거만 사용하고 "
                        "최종 답변만 출력하세요. "
                        "내부 추론 과정은 출력하지 마세요."
                    ),
                },
                {"role": "user", "content": str(prompt)},
            ],
            "topP": 0.8,
            "topK": 0,
            "maxTokens": self.config.max_tokens,
            "temperature": 0.1,
            "repetitionPenalty": 1.1,
            "stop": [],
        }

        try:
            response = self._http_client.post(
                self._url,
                headers=headers,
                json=body,
                timeout=self.config.timeout_sec,
            )
        except httpx.TimeoutException as exc:
            raise HcxTimeoutError("HCX request timed out") from exc
        except httpx.HTTPError as exc:
            raise HcxServiceError("HCX request failed") from exc

        if response.status_code in {401, 403}:
            raise HcxAuthenticationError("HCX authentication failed")
        if response.status_code < 200 or response.status_code >= 300:
            raise HcxServiceError("HCX service returned an unsuccessful response")

        try:
            payload = response.json()
        except (TypeError, ValueError) as exc:
            raise HcxResponseError("HCX returned malformed JSON") from exc
        if not isinstance(payload, Mapping):
            raise HcxResponseError("HCX returned an invalid response")

        status = payload.get("status")
        if not isinstance(status, Mapping) or str(status.get("code")) != "20000":
            raise HcxServiceError("HCX service rejected the request")
        result = payload.get("result")
        if not isinstance(result, Mapping):
            raise HcxResponseError("HCX response result is missing")
        if result.get("finishReason") == "length":
            raise HcxResponseError("HCX response was truncated")
        message = result.get("message")
        if not isinstance(message, Mapping):
            raise HcxResponseError("HCX response message is missing")
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise HcxResponseError("HCX returned an empty response")
        return content.strip()


def load_hcx_config(env: Mapping[str, str] | None = None) -> HcxConfig | None:
    values = env if env is not None else os.environ
    api_key = str(values.get("CLOVA_API_KEY") or "").strip()
    endpoint = str(values.get("CLOVA_ENDPOINT") or "").strip()
    model_name = str(values.get("MODEL_NAME") or "").strip()
    if not api_key:
        return None
    if not endpoint or not model_name:
        raise HcxConfigurationError(
            "CLOVA_API_KEY, CLOVA_ENDPOINT, and MODEL_NAME must be set together"
        )

    parsed = urlparse(endpoint)
    if parsed.scheme != "https" or not parsed.netloc:
        raise HcxConfigurationError("CLOVA_ENDPOINT must be an HTTPS URL")

    request_timeout = _positive_float(
        values.get("REQUEST_TIMEOUT"),
        default=_DEFAULT_REQUEST_TIMEOUT_SEC,
        name="REQUEST_TIMEOUT",
    )
    requested_hcx_timeout = _positive_float(
        values.get("HCX_TIMEOUT"),
        default=_DEFAULT_HCX_TIMEOUT_SEC,
        name="HCX_TIMEOUT",
    )
    available_hcx_budget = max(1.0, request_timeout - _REQUEST_BUDGET_RESERVE_SEC)
    timeout_sec = min(requested_hcx_timeout, available_hcx_budget)
    max_tokens = _positive_int(
        values.get("HCX_MAX_TOKENS"),
        default=_DEFAULT_MAX_TOKENS,
        name="HCX_MAX_TOKENS",
    )
    if max_tokens > 4096:
        raise HcxConfigurationError("HCX_MAX_TOKENS must not exceed 4096")
    return HcxConfig(
        api_key=api_key,
        endpoint=endpoint,
        model_name=model_name,
        timeout_sec=timeout_sec,
        max_tokens=max_tokens,
    )


def build_hcx_client_from_env(
    env: Mapping[str, str] | None = None,
    http_client: HttpClient | None = None,
) -> HcxClient | None:
    try:
        config = load_hcx_config(env)
    except HcxConfigurationError:
        logger.warning(
            "HCX is disabled because its environment configuration is invalid"
        )
        return None
    return HcxClient(config, http_client=http_client) if config is not None else None


def hcx_is_configured(env: Mapping[str, str] | None = None) -> bool:
    try:
        return load_hcx_config(env) is not None
    except HcxConfigurationError:
        return False


def _completion_url(endpoint: str, model_name: str) -> str:
    base = endpoint.rstrip("/")
    encoded_model = quote(model_name, safe="")
    if "/v3/chat-completions/" in base:
        prefix = base.split("/v3/chat-completions/", maxsplit=1)[0]
        return f"{prefix}/v3/chat-completions/{encoded_model}"
    if base.endswith("/v3/chat-completions"):
        return f"{base}/{encoded_model}"
    return f"{base}/v3/chat-completions/{encoded_model}"


def _positive_float(value: Any, *, default: float, name: str) -> float:
    if value is None or str(value).strip() == "":
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise HcxConfigurationError(f"{name} must be a positive number") from exc
    if parsed <= 0:
        raise HcxConfigurationError(f"{name} must be a positive number")
    return parsed


def _positive_int(value: Any, *, default: int, name: str) -> int:
    if value is None or str(value).strip() == "":
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise HcxConfigurationError(f"{name} must be a positive integer") from exc
    if parsed <= 0:
        raise HcxConfigurationError(f"{name} must be a positive integer")
    return parsed
