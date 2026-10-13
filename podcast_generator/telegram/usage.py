"""Per-user successful delivery counts stored in a local JSON file."""

import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Any


USAGE_FILE = Path(__file__).with_name("usage.json")
_usage_lock = asyncio.Lock()


class UsageStoreError(RuntimeError):
    """The local usage file could not be read or updated safely."""


def _read_usage(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"users": {}}
    try:
        usage = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UsageStoreError("The usage file could not be read.") from exc
    if not isinstance(usage, dict) or not isinstance(usage.get("users"), dict):
        raise UsageStoreError("The usage file has an invalid format.")
    for user_id, entry in usage["users"].items():
        if (
            not isinstance(user_id, str)
            or not user_id.isdecimal()
            or not isinstance(entry, dict)
            or not isinstance(entry.get("username", ""), str)
            or not isinstance(entry.get("successful_deliveries"), int)
            or isinstance(entry.get("successful_deliveries"), bool)
            or entry["successful_deliveries"] < 0
        ):
            raise UsageStoreError("The usage file has an invalid format.")
    return usage


def _write_usage(path: Path, usage: dict[str, Any]) -> None:
    temporary_path: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            json.dump(usage, temporary_file, ensure_ascii=True, indent=2)
            temporary_file.write("\n")
        os.replace(temporary_path, path)
    except OSError as exc:
        raise UsageStoreError("The usage file could not be saved.") from exc
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _usage_counts(usage: dict[str, Any], user_id: int) -> dict[str, int]:
    users = usage["users"]
    total = sum(
        entry["successful_deliveries"] for entry in users.values()
    )
    user = users.get(str(user_id), {})
    return {
        "total_successful_deliveries": total,
        "user_successful_deliveries": user.get("successful_deliveries", 0),
    }


async def get_usage(user_id: int | None = None) -> dict[str, int]:
    """Return the global successful-delivery total and optional user total."""
    async with _usage_lock:
        usage = await asyncio.to_thread(_read_usage, USAGE_FILE)
    return _usage_counts(usage, user_id or 0)


async def record_successful_delivery(
    user_id: int, username: str = ""
) -> dict[str, int]:
    """Persist one confirmed audio delivery and return updated totals."""
    async with _usage_lock:
        usage = await asyncio.to_thread(_read_usage, USAGE_FILE)
        user_key = str(user_id)
        entry = usage["users"].setdefault(
            user_key,
            {"username": "", "successful_deliveries": 0},
        )
        if username:
            entry["username"] = username[:64]
        entry["successful_deliveries"] += 1
        await asyncio.to_thread(_write_usage, USAGE_FILE, usage)
        return _usage_counts(usage, user_id)
