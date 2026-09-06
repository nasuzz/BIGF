"""Docker readiness probe for the API's backward-compatible health response."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen


DEFAULT_HEALTH_URL = "http://localhost:8000/health"
DEFAULT_TIMEOUT_SECONDS = 4.0


def response_is_ready(payload: Any) -> bool:
    """Return true only when the API and its required database are ready."""
    return (
        isinstance(payload, dict)
        and payload.get("status") == "ok"
        and payload.get("database") == "connected"
    )


def check_readiness(
    url: str = DEFAULT_HEALTH_URL,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    opener: Callable[..., Any] = urlopen,
) -> bool:
    try:
        with opener(url, timeout=timeout) as response:
            payload = json.load(response)
    except (OSError, URLError, ValueError, json.JSONDecodeError):
        return False
    return response_is_ready(payload)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    url = args[0] if args else DEFAULT_HEALTH_URL
    return 0 if check_readiness(url) else 1


if __name__ == "__main__":
    raise SystemExit(main())
