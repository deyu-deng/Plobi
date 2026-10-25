"""OpenCode — spawn-based chat + OpenCode Go plan usage read.

Chat (compliant): the user's already-signed-in ``opencode`` binary runs one turn:

    opencode run "<prompt>" --model provider/model

Quota: an OpenCode Go subscription has rolling 5h / weekly / monthly windows.
The user's own key is in ``$XDG_DATA_HOME/opencode/auth.json`` (Windows:
``%LOCALAPPDATA%`` fallback, but OpenCode uses ``~/.local/share/opencode``
on every platform). The official usage endpoint is:

    GET https://opencode.ai/zen/go/v1/usage

No browser, no dashboard. Zen free-tier / bring-your-own-provider keys have no
unified quota endpoint — in that case the snapshot stays empty rather than
inventing a number.

Install : npm i -g opencode-ai  (or curl -fsSL https://opencode.ai/install | bash)
Verify  : opencode --version ; opencode run "say hi"
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from ...base import Account
from ...cli import CliProvider

logger = logging.getLogger("quota_hub.opencode")

USAGE_URL = "https://opencode.ai/zen/go/v1/usage"


def _auth_path(config: dict) -> Path:
    explicit = str(config.get("auth_file") or "").strip()
    if explicit:
        return Path(explicit)
    root = os.environ.get("XDG_DATA_HOME", "").strip()
    base = Path(root) if root else Path.home() / ".local" / "share"
    return base / "opencode" / "auth.json"


def _load_go_key(config: dict) -> str:
    env_key = (os.environ.get("OPENCODE_GO_API_KEY") or "").strip()
    if env_key:
        return env_key
    try:
        data = json.loads(_auth_path(config).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    if not isinstance(data, dict):
        return ""
    for name in ("opencode-go", "opencode_go", "opencode"):
        entry = data.get(name)
        if isinstance(entry, dict):
            key = entry.get("key") or entry.get("apiKey") or entry.get("access")
            if isinstance(key, str) and key.strip():
                return key.strip()
    # Some builds store a flat {"key": "sk-..."} for a single provider.
    key = data.get("key")
    return key.strip() if isinstance(key, str) else ""


def _parse_opencode_usage(data: dict) -> list[dict]:
    usage = data.get("usage")
    if not isinstance(usage, dict):
        usage = data
    windows = (
        ("rolling", "5h", "5h"),
        ("weekly", "weekly", "weekly"),
        ("monthly", "monthly", "monthly"),
    )
    buckets: list[dict] = []
    for key, label, window in windows:
        block = usage.get(key)
        if not isinstance(block, dict):
            continue
        percent = block.get("percent")
        if percent is None:
            percent = block.get("used_percent")
        try:
            used = float(percent) / 100.0
        except (TypeError, ValueError):
            continue
        remaining = max(0.0, min(1.0, 1.0 - used))
        buckets.append(
            {
                "bucket_id": f"opencode-{key}",
                "display_name": f"OpenCode Go {label}",
                "remaining_fraction": round(remaining, 4),
                "reset_time": str(block.get("resetsAt") or block.get("resets_at") or ""),
                "window": window,
            }
        )
    if not buckets:
        return []
    return [{"display_name": "OpenCode Go", "buckets": buckets}]


class OpenCodeCliProvider(CliProvider):
    name = "opencode_cli"
    binary = "opencode"
    args_template = ["run", "{model_flag}", "{prompt}"]
    model_flag = "--model"
    timeout = 300.0
    isolate_workspace = True
    stream_from_chat = True
    served_models = ("opencode/default",)
    model_map = {"opencode/default": ""}

    async def refresh_quota(self, acc: Account) -> None:
        key = _load_go_key(self.config)
        if not key:
            logger.info("opencode quota skipped: no OpenCode Go key in auth.json")
            return
        headers = {
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            "User-Agent": "quota_hub/1.0",
        }
        try:
            response = await self.http.get(USAGE_URL, headers=headers, timeout=20.0)
        except Exception as exc:
            logger.warning("opencode quota request failed: %s: %s", type(exc).__name__, exc)
            return
        if response.status_code != 200:
            logger.warning("opencode quota endpoint returned %s", response.status_code)
            return
        try:
            data = response.json()
        except Exception:
            return
        groups = _parse_opencode_usage(data if isinstance(data, dict) else {})
        if not groups:
            return
        previous = dict(acc.quota or {})
        previous["groups"] = groups
        previous["checked_at"] = int(time.time())
        previous["source"] = "opencode:zen/go/v1/usage"
        acc.quota = previous
