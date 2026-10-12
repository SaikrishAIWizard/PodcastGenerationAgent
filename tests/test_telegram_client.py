import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from podcast_generator.telegram.client import TelegramApiError, TelegramClient


class TelegramClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_call_retries_temporary_connection_errors(self):
        attempts = 0

        def respond(request):
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise httpx.ConnectError("temporary connection failure", request=request)
            return httpx.Response(200, json={"ok": True, "result": {}})

        client = TelegramClient("test-token", transport=httpx.MockTransport(respond))
        with patch(
            "podcast_generator.telegram.client.asyncio.sleep",
            new_callable=AsyncMock,
        ) as sleep:
            result = await client.call("sendMessage", chat_id=123, text="Hello")

        self.assertEqual(result, {})
        self.assertEqual(attempts, 3)
        self.assertEqual(
            [call.args[0] for call in sleep.await_args_list],
            [0.5, 1.0],
        )

    async def test_call_reports_connection_error_after_retries(self):
        attempts = 0

        def respond(request):
            nonlocal attempts
            attempts += 1
            raise httpx.ConnectError("connection unavailable", request=request)

        client = TelegramClient("test-token", transport=httpx.MockTransport(respond))
        with (
            patch(
                "podcast_generator.telegram.client.asyncio.sleep",
                new_callable=AsyncMock,
            ) as sleep,
            self.assertRaises(TelegramApiError) as raised,
        ):
            await client.call("sendMessage", chat_id=123, text="Hello")

        self.assertEqual(attempts, 5)
        self.assertEqual(sleep.await_count, 4)
        self.assertEqual(raised.exception.status_code, 503)
        self.assertIsInstance(raised.exception.__cause__, httpx.ConnectError)

    async def test_download_retries_temporary_connection_errors(self):
        attempts = 0

        def respond(request):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise httpx.ConnectError("temporary connection failure", request=request)
            return httpx.Response(200, content=b"telegram file")

        client = TelegramClient("test-token", transport=httpx.MockTransport(respond))
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / "download.txt"
            with patch(
                "podcast_generator.telegram.client.asyncio.sleep",
                new_callable=AsyncMock,
            ):
                size = await client.download_file(
                    "documents/file.txt", destination, max_bytes=1024
                )

            self.assertEqual(size, len(b"telegram file"))
            self.assertEqual(destination.read_bytes(), b"telegram file")
        self.assertEqual(attempts, 2)


if __name__ == "__main__":
    unittest.main()
