"""Anthropic Claude Code — spawn-based chat + native subscription usage read.

Chat (compliant): the user's already-signed-in ``claude`` binary runs one turn:

    claude -p "<prompt>" --output-format json --model <model>

``--output-format json`` prints one JSON object (``result`` / ``is_error`` /
``usage``). We take ``result``; an errored run is an error, never a completion.

Quota: on subscription sign-in Claude Code stores an OAuth access token under
``$CLAUDE_CONFIG_DIR/.credentials.json`` (default ``~/.claude``). Anthropic's own
usage endpoint returns the rolling 5-hour and 7-day windows:

    GET https://api.anthropic.com/api/oauth/usage

No browser, no dashboard, no second process. If the token is absent/expired the
last snapshot is kept; we never write zeros.

Install : npm i -g @anthropic-ai/claude-code
Verify  : claude --version ; claude -p "say hi" --output-format json
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from ...base import Account, UpstreamError
from ...cli import CliProvider

logger = logging.getLogger("quota_hub.claude_code")

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"


def _parse_claude_result(stdout: str) -> tuple[str, dict]:
    """Return (answer text, OpenAI usage) from ``--output-format json``."""
    text = stdout.strip()
    start = text.find("{")
    if start < 0:
        raise UpstreamError(502, "claude: JSON output not found")
    try:
        data = json.loads(text[start:])
    except json.JSONDecodeError as exc:
        raise UpstreamError(502, f"claude: unreadable JSON output: {exc}") from exc
    if not isinstance(data, dict):
        raise UpstreamError(502, "claude: JSON output is not an object")
    message = str(data.get("result") or "").strip()
    if data.get("is_error") or data.get("subtype") in {"error", "error_max_turns"}:
        raise UpstreamError(502, f"claude: {message or data.get('subtype') or 'run failed'}")
    usage = {}
    raw = data.get("usage")
    if isinstance(raw, dict):
        prompt = int(raw.get("input_tokens") or 0)
        completion = int(raw.get("output_tokens") or 0)
        usage = {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
        }
    return message, usage


def _load_oauth_token(config: dict) -> str:
    token = (os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or "").strip()
    if token:
        return token
    token_file = str(config.get("credentials_file") or "").strip()
    if not token_file:
        root = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
        base = Path(root) if root else Path.home() / ".claude"
        token_file = str(base / ".credentials.json")
    try:
        data = json.loads(Path(token_file).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    if not isinstance(data, dict):
        return ""
    oauth = data.get("claudeAiOauth")
    if not isinstance(oauth, dict):
        oauth = data
    return str(oauth.get("accessToken") or oauth.get("access_token") or "").strip()


def _used_fraction(value) -> float | None:
    try:
        used = float(value)
    except (TypeError, ValueError):
        return None
    # The endpoint reports percent (e.g. 18.0). Tolerate a 0..1 fraction too.
    fraction = used if 0.0 <= used <= 1.0 else used / 100.0
    return max(0.0, min(1.0, fraction))


def _parse_claude_usage(data: dict) -> list[dict]:
    """Normalize the oauth usage payload into groups/buckets."""
    windows = (
        ("five_hour", "5h", "5h"),
        ("seven_day", "weekly", "weekly"),
        ("seven_day_opus", "weekly · Opus", "weekly"),
        ("seven_day_sonnet", "weekly · Sonnet", "weekly"),
    )
    buckets: list[dict] = []
    for key, label, window in windows:
        block = data.get(key)
        if not isinstance(block, dict):
            continue
        used = block.get("utilization")
        if used is None:
            used = block.get("used_percent")
        fraction = _used_fraction(used)
        if fraction is None:
            continue
        reset = block.get("resets_at") or block.get("reset_at") or ""
        buckets.append(
            {
                "bucket_id": f"claude-{key}",
                "display_name": f"Claude Code {label}",
                "remaining_fraction": round(max(0.0, 1.0 - fraction), 4),
                "reset_time": str(reset or ""),
                "window": window,
            }
        )
    if not buckets:
        return []
    return [{"display_name": "Claude Code", "buckets": buckets}]


class ClaudeCodeCliProvider(CliProvider):
    name = "claude_code_cli"
    binary = "claude"
    args_template = ["{model_flag}", "-p", "{prompt}", "--output-format", "json"]
    model_flag = "--model"
    timeout = 300.0
    isolate_workspace = True
    stream_from_chat = True
    served_models = (
        "claude/sonnet-4-6",
        "claude/opus-4-6",
        "claude/haiku-4-5",
    )
    model_map = {
        "claude/sonnet-4-6": "claude-sonnet-4-6",
        "claude/opus-4-6": "claude-opus-4-6",
        "claude/haiku-4-5": "claude-haiku-4-5",
    }

    def parse_response(self, stdout: str, oai_req: dict) -> dict:
        text, usage = _parse_claude_result(stdout)
        if not text:
            raise UpstreamError(502, "claude: empty result")
        response = super().parse_response(text, oai_req)
        if usage:
            response["usage"] = usage
        return response

    async def refresh_quota(self, acc: Account) -> None:
        token = _load_oauth_token(self.config)
        if not token:
            logger.info("claude quota skipped: no OAuth credential on this machine")
            return
        headers = {
            "Authorization": f"Bearer {token}",
            "anthropic-beta": "oauth-2025-04-20",
            "anthropic-version": "2023-06-01",
            "Accept": "application/json",
            "User-Agent": "quota_hub/1.0",
        }
        try:
            response = await self.http.get(USAGE_URL, headers=headers, timeout=20.0)
        except Exception as exc:
            logger.warning("claude quota request failed: %s: %s", type(exc).__name__, exc)
            return
        if response.status_code != 200:
            logger.warning("claude quota endpoint returned %s", response.status_code)
            return
        try:
            data = response.json()
        except Exception:
            return
        groups = _parse_claude_usage(data if isinstance(data, dict) else {})
        if not groups:
            return
        previous = dict(acc.quota or {})
        previous["groups"] = groups
        previous["checked_at"] = int(time.time())
        previous["source"] = "claude_code:api.anthropic.com/api/oauth/usage"
        acc.quota = previous
