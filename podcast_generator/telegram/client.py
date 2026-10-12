"""Telegram Bot API and LLM key-validation HTTP clients."""

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx

from ..settings import PROVIDERS

TELEGRAM_CONNECT_ATTEMPTS = 5
TELEGRAM_CONNECT_RETRY_DELAY_SECONDS = 0.5


class TelegramApiError(RuntimeError):
    """A Telegram API request failed without exposing its response body."""

    def __init__(self, method: str, status_code: int, retry_after: float = 0) -> None:
        super().__init__(f"Telegram {method} request failed ({status_code}).")
        self.method = method
        self.status_code = status_code
        self.retry_after = retry_after

    @property
    def retryable(self) -> bool:
        return self.status_code >= 500 or self.status_code in {408, 429}


class ProviderValidationError(RuntimeError):
    """An LLM API key could not be validated."""


class TelegramClient:
    def __init__(
        self,
        token: str,
        *,
        timeout: float = 20,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._token = token
        self._timeout = timeout
        self._transport = transport

    def _api_url(self, method: str) -> str:
        return f"https://api.telegram.org/bot{self._token}/{method}"

    async def _post_with_retries(
        self,
        method: str,
        send: Callable[[httpx.AsyncClient], Awaitable[httpx.Response]],
    ) -> httpx.Response:
        async with httpx.AsyncClient(
            timeout=self._timeout, transport=self._transport
        ) as client:
            for attempt in range(TELEGRAM_CONNECT_ATTEMPTS):
                try:
                    return await send(client)
                except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                    if attempt == TELEGRAM_CONNECT_ATTEMPTS - 1:
                        raise TelegramApiError(method, 503) from exc
                    await asyncio.sleep(
                        TELEGRAM_CONNECT_RETRY_DELAY_SECONDS * (2**attempt)
                    )
                except httpx.HTTPError as exc:
                    raise TelegramApiError(method, 503) from exc
        raise RuntimeError("Telegram request retry loop ended unexpectedly.")

    async def call(self, method: str, **data: Any) -> dict[str, Any]:
        response = await self._post_with_retries(
            method,
            lambda client: client.post(self._api_url(method), json=data),
        )

        payload: dict[str, Any] = {}
        try:
            decoded = response.json()
            if isinstance(decoded, dict):
                payload = decoded
        except ValueError:
            pass
        if response.is_error or payload.get("ok") is not True:
            parameters = payload.get("parameters")
            retry_after = (
                parameters.get("retry_after", 0)
                if isinstance(parameters, dict)
                else 0
            )
            raise TelegramApiError(
                method,
                response.status_code,
                float(retry_after) if isinstance(retry_after, (int, float)) else 0,
            )
        result = payload.get("result")
        return result if isinstance(result, dict) else {}

    async def send_audio(
        self, chat_id: int, audio_path: Path, filename: str
    ) -> dict[str, Any]:
        async def send(client: httpx.AsyncClient) -> httpx.Response:
            with audio_path.open("rb") as audio_file:
                return await client.post(
                    self._api_url("sendAudio"),
                    data={"chat_id": str(chat_id)},
                    files={"audio": (filename, audio_file, "audio/mpeg")},
                )

        try:
            response = await self._post_with_retries("sendAudio", send)
        except OSError as exc:
            raise TelegramApiError("sendAudio", 500) from exc

        payload: dict[str, Any] = {}
        try:
            decoded = response.json()
            if isinstance(decoded, dict):
                payload = decoded
        except ValueError:
            pass
        if response.is_error or payload.get("ok") is not True:
            parameters = payload.get("parameters")
            retry_after = (
                parameters.get("retry_after", 0)
                if isinstance(parameters, dict)
                else 0
            )
            raise TelegramApiError(
                "sendAudio",
                response.status_code,
                float(retry_after) if isinstance(retry_after, (int, float)) else 0,
            )
        result = payload.get("result")
        return result if isinstance(result, dict) else {}

    async def download_file(
        self, file_path: str, destination: Path, max_bytes: int
    ) -> int:
        async with httpx.AsyncClient(
            timeout=self._timeout, transport=self._transport
        ) as client:
            for attempt in range(TELEGRAM_CONNECT_ATTEMPTS):
                try:
                    async with client.stream(
                        "GET",
                        f"https://api.telegram.org/file/bot{self._token}/{file_path}",
                        follow_redirects=True,
                    ) as response:
                        response.raise_for_status()
                        size = 0
                        with destination.open("wb") as output:
                            async for chunk in response.aiter_bytes(64 * 1024):
                                size += len(chunk)
                                if size > max_bytes:
                                    raise ValueError(
                                        "The uploaded file exceeds the size limit."
                                    )
                                output.write(chunk)
                        return size
                except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                    if attempt == TELEGRAM_CONNECT_ATTEMPTS - 1:
                        raise TelegramApiError("file download", 503) from exc
                    await asyncio.sleep(
                        TELEGRAM_CONNECT_RETRY_DELAY_SECONDS * (2**attempt)
                    )
                except httpx.HTTPError as exc:
                    raise TelegramApiError("file download", 503) from exc
        raise RuntimeError("Telegram file download retry loop ended unexpectedly.")


async def validate_provider_key(provider: str, api_key: str) -> None:
    """Validate a provider key without including it in diagnostics."""
    if provider not in PROVIDERS:
        raise ProviderValidationError("Unsupported language-model provider.")
    if not api_key.strip():
        raise ProviderValidationError("The API key cannot be empty.")

    if provider == "gemini":
        url = "https://generativelanguage.googleapis.com/v1beta/models"
        headers = {"x-goog-api-key": api_key.strip()}
    elif provider == "groq":
        url = "https://api.groq.com/openai/v1/models"
        headers = {"Authorization": f"Bearer {api_key.strip()}"}
    else:
        url = "https://api.openai.com/v1/models"
        headers = {"Authorization": f"Bearer {api_key.strip()}"}

    try:
        async with httpx.AsyncClient(timeout=8) as client:
            response = await client.get(url, headers=headers)
    except httpx.TimeoutException as exc:
        raise ProviderValidationError(
            "Could not validate the key right now. Please try again."
        ) from exc
    except httpx.HTTPError as exc:
        raise ProviderValidationError(
            "Could not reach the provider to validate the key. Please try again."
        ) from exc

    if response.status_code in {401, 403}:
        raise ProviderValidationError(
            "That API key was rejected. Check it and send it again."
        )
    if response.status_code == 429:
        raise ProviderValidationError(
            "The provider is rate limiting key validation. Please try again shortly."
        )
    if response.is_error:
        raise ProviderValidationError(
            "The provider could not validate this key right now. Please try again."
        )
