"""HTTP routes for memory-provider OAuth connect, mounted by ``web_server``.

Kept out of ``web_server.py`` so the memory feature's surface stays in the
memory layer. Dispatch is by convention: a provider's flow lives at
``plugins.memory.<provider>.oauth_flow`` exposing ``start_loopback_flow_background``
and ``get_flow_status``; a provider without that module simply 404s. No provider
is named here.
"""

from __future__ import annotations

import importlib
import logging
from contextlib import contextmanager
from typing import Optional

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/memory/providers")
logger = logging.getLogger(__name__)


def _resolve_flow(provider: str):
    """Return a provider's OAuth flow module by convention, or raise 404."""
    if not provider.isidentifier():
        raise HTTPException(status_code=404, detail=f"unknown memory provider {provider!r}")
    try:
        return importlib.import_module(f"plugins.memory.{provider}.oauth_flow")
    except ImportError:
        raise HTTPException(status_code=404, detail=f"{provider} does not support OAuth connect")


@contextmanager
def _scope_to_profile(profile: Optional[str]):
    """Scope config resolution to ``profile`` **only when that home still exists**.

    ``None`` / ``""`` / ``"current"`` leaves resolution untouched（原行为）。

    **10-26 改判（裁定 178/181 之后）**：per-agent 的家目录已经取消，记忆落在
    ``memory/agents/<slug>/``。桌面仍会把「当前那一行的 profile 名」作为
    ``profile=`` 带上来，旧实现于是对名册里每一个没有家的行都回 404
    ``Profile 'x' does not exist.``——一次排查里现读 27 条，而被它挡住的只是
    「读一眼 OAuth 状态」这种只读动作，弹层因此在加载态上悬着不动。

    现在的判据：
    * 名字合法、家也在 ⇒ 照旧 override（老机器上还有家的行不受影响）；
    * 家不在 ⇒ **不 override、按共享家继续**（这正是裁 178 的终态：一个家）；
    * 名字不合法（路径穿越之类）⇒ 仍然 400，那不是"过时"，是脏输入。
    """
    requested = (profile or "").strip()
    if not requested or requested.lower() == "current":
        yield
        return

    from plobi_cli import agent_identity as profiles_mod
    from plobi_constants import reset_plobi_home_override, set_plobi_home_override

    try:
        profiles_mod.validate_profile_name(requested)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not profiles_mod.profile_exists(requested):
        logger.debug(
            "memory oauth: profile %r 没有家（裁 178 之后按共享家解析），不 override",
            requested,
        )
        yield
        return

    token = set_plobi_home_override(str(profiles_mod.get_profile_dir(requested)))
    try:
        yield
    finally:
        reset_plobi_home_override(token)


@router.post("/{provider}/oauth/start")
async def start_memory_oauth(provider: str, profile: Optional[str] = None):
    """Begin a provider's zero-CLI OAuth flow — opens the browser and captures
    the grant via the loopback listener. Returns immediately; poll status."""
    flow = _resolve_flow(provider)
    try:
        # The flow resolves its config path eagerly inside this scope; the worker
        # thread it spawns outlives the request and the override.
        with _scope_to_profile(profile):
            return flow.start_loopback_flow_background()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to start {provider} OAuth: {exc}")


@router.get("/{provider}/oauth/status")
async def memory_oauth_status(provider: str, profile: Optional[str] = None):
    """Poll a provider's OAuth flow: idle | pending | connected | error."""
    flow = _resolve_flow(provider)
    try:
        with _scope_to_profile(profile):
            return flow.get_flow_status()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read {provider} OAuth status: {exc}")
