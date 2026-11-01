"""OpenAI Codex CLI — spawn-based chat + native rate-limit read.

Chat (compliant): the user's already-signed-in ``codex`` binary runs one turn:

    codex exec "<prompt>" --json --skip-git-repo-check --sandbox read-only -m <model>

``--json`` prints a JSONL event stream. We only take the final ``agent_message``
(``item.completed``) and the token usage from ``turn.completed`` — never the whole
stream, never intermediate reasoning/tool events.

Quota: Codex stores its OAuth/API credential in ``$CODEX_HOME/auth.json``
(default ``~/.codex/auth.json``). The same credential can ask ChatGPT's own usage
endpoint ``/backend-api/wham/usage`` for the current 5h / weekly windows. This is
the capability the user asked us to internalize: no browser, no dashboard, no
second process. Failure is data — the previous snapshot stays.

Install : npm i -g @openai/codex
Verify  : codex --version ; codex exec --json "say hi"
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from ...base import Account, UpstreamError
from ...cli import CliProvider

logger = logging.getLogger("quota_hub.codex")

USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"


def _json_lines(stdout: str) -> list[dict]:
    rows: list[dict] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def _parse_codex_events(stdout: str) -> tuple[str, dict]:
    """Return (final agent message, OpenAI usage dict) from ``exec --json``.

    The stream is JSONL. Older/newer builds may add event types; we deliberately
    read only the stable pair:
      * ``item.completed`` with ``item.type == "agent_message"`` -> answer text
      * ``turn.completed`` with ``usage`` -> token counts
    An ``error`` / ``turn.failed`` event with no answer becomes an UpstreamError
    instead of an empty completion.
    """
    text = ""
    usage: dict = {}
    error = ""
    for event in _json_lines(stdout):
        kind = str(event.get("type") or "")
        if kind == "item.completed":
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "agent_message":
                candidate = item.get("text")
                if isinstance(candidate, str) and candidate.strip():
                    text = candidate.strip()
        elif kind == "turn.completed":
            raw = event.get("usage")
            if isinstance(raw, dict):
                prompt = int(raw.get("input_tokens") or 0)
                completion = int(raw.get("output_tokens") or 0)
                usage = {
                    "prompt_tokens": prompt,
                    "completion_tokens": completion,
                    "total_tokens": prompt + completion,
                }
        elif kind in {"error", "turn.failed"}:
            message = event.get("message") or event.get("error")
            if isinstance(message, dict):
                message = message.get("message") or message.get("code")
            error = str(message or kind)
    if not text and error:
        raise UpstreamError(502, f"codex: {error}")
    return text, usage


def _load_auth(config: dict) -> tuple[str, str]:
    """Return (access_token, account_id) from env or ``$CODEX_HOME/auth.json``."""
    token = os.environ.get("CODEX_ACCESS_TOKEN", "").strip()
    account_id = os.environ.get("CODEX_ACCOUNT_ID", "").strip()
    if token:
        return token, account_id

    auth_file = str(config.get("auth_file") or "").strip()
    if not auth_file:
        home = os.environ.get("CODEX_HOME", "").strip()
        base = Path(home) if home else Path.home() / ".codex"
        auth_file = str(base / "auth.json")
    try:
        data = json.loads(Path(auth_file).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "", ""
    if not isinstance(data, dict):
        return "", ""
    tokens = data.get("tokens") if isinstance(data.get("tokens"), dict) else data
    token = str(tokens.get("access_token") or tokens.get("accessToken") or "").strip()
    account_id = str(
        tokens.get("account_id")
        or data.get("account_id")
        or tokens.get("accountId")
        or ""
    ).strip()
    return token, account_id


def _window_label(seconds: int) -> str:
    if seconds <= 0:
        return "window"
    if seconds >= 7 * 24 * 3600:
        return "weekly"
    if seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    return f"{round(seconds / 60)}m"


def _reset_at(window: dict, now: float) -> str:
    for key in ("resets_at", "reset_at", "resetsAt"):
        value = window.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(value))
        if isinstance(value, str) and value.strip():
            return value.strip()
    after = window.get("reset_after_seconds")
    if isinstance(after, (int, float)):
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + float(after)))
    return ""


def _parse_codex_usage(data: dict, now: float | None = None) -> list[dict]:
    """Normalize ``/wham/usage`` into the hub's groups/buckets shape."""
    now = time.time() if now is None else now
    rate = data.get("rate_limit")
    if not isinstance(rate, dict):
        rate = data.get("rate_limits") if isinstance(data.get("rate_limits"), dict) else {}
    windows: list[tuple[str, dict]] = []
    for key in ("primary_window", "secondary_window"):
        window = rate.get(key)
        if isinstance(window, dict):
            windows.append((key, window))
    extra = data.get("additional_rate_limits")
    if isinstance(extra, dict):
        windows.extend((str(k), v) for k, v in extra.items() if isinstance(v, dict))
    elif isinstance(extra, list):
        for item in extra:
            if not isinstance(item, dict):
                continue
            window = item.get("rate_limit") if isinstance(item.get("rate_limit"), dict) else item
            name = str(item.get("name") or item.get("model") or "additional")
            windows.append((name, window))

    buckets: list[dict] = []
    for name, window in windows:
        used = window.get("used_percent")
        if used is None:
            used = window.get("usedPercent")
        try:
            used_fraction = float(used) / 100.0
        except (TypeError, ValueError):
            continue
        remaining = max(0.0, min(1.0, 1.0 - used_fraction))
        seconds = window.get("limit_window_seconds")
        if seconds is None and window.get("window_minutes") is not None:
            try:
                seconds = int(window["window_minutes"]) * 60
            except (TypeError, ValueError):
                seconds = 0
        try:
            seconds = int(seconds or 0)
        except (TypeError, ValueError):
            seconds = 0
        label = _window_label(seconds)
        buckets.append(
            {
                "bucket_id": f"codex-{name}",
                "display_name": f"Codex {label}",
                "remaining_fraction": round(remaining, 4),
                "reset_time": _reset_at(window, now),
                "window": label,
            }
        )
    if not buckets:
        return []
    return [{"display_name": "Codex", "buckets": buckets}]


class CodexCliProvider(CliProvider):
    name = "codex_cli"
    binary = "codex"
    prompt_flag = ""  # prompt is positional after `exec`
    args_template = [
        "exec",
        "{model_flag}",
        "{prompt}",
        "--json",
        "--skip-git-repo-check",
        "--sandbox",
        "read-only",
    ]
    model_flag = "--model"
    timeout = 300.0
    isolate_workspace = True
    stream_from_chat = True
    served_models = (
        "codex_cli/gpt-5.4",
        "codex_cli/gpt-5.4-mini",
        "codex_cli/gpt-5.3-codex",
    )
    model_map = {
        "codex_cli/gpt-5.4": "gpt-5.4",
        "codex_cli/gpt-5.4-mini": "gpt-5.4-mini",
        "codex_cli/gpt-5.3-codex": "gpt-5.3-codex",
    }

    def parse_response(self, stdout: str, oai_req: dict) -> dict:
        text, usage = _parse_codex_events(stdout)
        if not text:
            raise UpstreamError(502, "codex: exec --json produced no agent_message")
        response = super().parse_response(text, oai_req)
        if usage:
            response["usage"] = usage
        return response

    async def refresh_quota(self, acc: Account) -> None:
        token, account_id = _load_auth(self.config)
        if not token:
            logger.info("codex quota skipped: no %s", "CODEX_HOME/auth.json or CODEX_ACCESS_TOKEN")
            return
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "quota_hub/1.0",
        }
        if account_id:
            headers["chatgpt-account-id"] = account_id
        try:
            response = await self.http.get(USAGE_URL, headers=headers, timeout=20.0)
        except Exception as exc:
            logger.warning("codex quota request failed: %s: %s", type(exc).__name__, exc)
            return
        if response.status_code != 200:
            logger.warning("codex quota endpoint returned %s", response.status_code)
            return
        try:
            data = response.json()
        except Exception:
            return
        groups = _parse_codex_usage(data if isinstance(data, dict) else {})
        if not groups:
            return
        previous = dict(acc.quota or {})
        previous["groups"] = groups
        previous["checked_at"] = int(time.time())
        previous["source"] = "codex:chatgpt-backend-api/wham/usage"
        acc.quota = previous
