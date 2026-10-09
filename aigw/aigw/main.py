"""OpenAI-compatible HTTP surface.

Endpoints:
  GET  /v1/models
  POST /v1/chat/completions   (stream + non-stream)
  POST /v1/embeddings
  GET  /healthz               (pool health + token health + version)

Downstream auth: a single local API key (sk-...) so only your own software can call
this gateway. Upstream account selection / token refresh is handled by the Scheduler
and TokenManager.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from . import __version__
from .config import load
from .providers.base import UpstreamError
from .registry import Registry
from .scheduler import NoAccountAvailable, Scheduler
from .tokens.manager import TokenManager
from .tokens.vault import Vault

logger = logging.getLogger("aigw")

# Keys that are fine for local mock demos but dangerous if left in place. This is
# a *classification* of known-insecure values, not a source of the gateway's key:
# the shipped local default lives in exactly one place — `server.api_key` in the
# config this process is started with (裁定 86 甲: this service owns the value).
# It is deliberately NOT repeated here; the `sk-local-` prefix check below already
# covers every `<app>/config*.yaml` default of that shape, so listing it would add
# a second copy that goes stale the moment someone changes the config.
_WEAK_API_KEYS = frozenset(
    {
        "sk-test",
        "changeme",
        "secret",
    }
)


def _declared_api_key(cfg: dict) -> str:
    """Read the gateway's downstream key from its one owner: ``server.api_key``.

    No built-in fallback (裁定 86 甲). A config that omits the key, or declares it
    empty, is a startup error rather than an open hub: 裁定 94 reads an empty string
    as "not declared", never as "declared as no authentication". Failing here also
    means `_auth()` can never compare against a value nobody configured.
    """
    server = cfg.get("server") if isinstance(cfg.get("server"), dict) else {}
    if "api_key" not in server:
        raise RuntimeError(
            "aigw: server.api_key is not declared in the gateway config. This service "
            "owns that value and carries no built-in fallback — set it (or "
            "${AIGW_KEY:-…} in the YAML) before starting the hub."
        )
    key = str(server.get("api_key") or "").strip()
    if not key:
        raise RuntimeError(
            "aigw: server.api_key is declared empty. An empty string means 'not "
            "declared' (裁定 94), not 'no authentication' — refuse to start rather "
            "than serve an unauthenticated quota hub."
        )
    return key


def setup_logging(cfg: dict | None = None) -> None:
    """Global logging: INFO to console, plus an optional file sink.

    cfg keys: level (DEBUG/INFO/WARNING), file (path), format (strftime-style).
    """
    cfg = cfg or {}
    level = getattr(logging, str(cfg.get("level", "INFO")).upper(), logging.INFO)
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if cfg.get("file"):
        handlers.append(logging.FileHandler(cfg["file"], encoding="utf-8"))
    logging.basicConfig(
        level=level,
        format=cfg.get("format", "%(asctime)s %(levelname)-7s %(name)s: %(message)s"),
        handlers=handlers,
        force=True,
    )


app = FastAPI(title="aigw — unified desktop-quota gateway", version=__version__)
STATE: dict = {}


def _startup_safety_checks(cfg: dict) -> None:
    """Warn (do not hard-fail) on common footguns so demos still boot.

    One exception, deliberately: a missing or empty ``server.api_key`` is not a
    warning but a startup error — see :func:`_declared_api_key`.
    """
    server = cfg.get("server") or {}
    host = str(server.get("host", "127.0.0.1"))
    key = _declared_api_key(cfg)
    if host in ("0.0.0.0", "::", "[::]"):
        logger.warning(
            "server.host=%s exposes aigw on all interfaces — keep a strong api_key "
            "and prefer a reverse proxy; do not rely on obscurity.",
            host,
        )
    if not key or key in _WEAK_API_KEYS or key.startswith("sk-local-"):
        logger.warning(
            "weak/default api_key in use (%r). Fine for mock/local demos; set "
            "AIGW_KEY to a high-entropy secret before pointing real clients at aigw.",
            key[:16] + ("…" if len(key) > 16 else ""),
        )


@app.on_event("startup")
async def _startup():
    cfg = load()
    setup_logging(cfg.get("logging"))
    _startup_safety_checks(cfg)
    # trust_env defaults to True so the gateway honours HTTP(S)_PROXY in the
    # environment (e.g. a local V2rayU / Clash proxy). Set trust_env: false in
    # config only if you specifically need direct egress.
    http = httpx.AsyncClient(
        timeout=httpx.Timeout(120.0, connect=10.0), http2=True, trust_env=cfg.get("trust_env", True)
    )
    reg = await Registry(cfg, http).build()
    # Eagerly refresh each provider's model catalog from upstream (Antigravity
    # fetches its real model list via fetchAvailableModels). Best-effort and
    # non-blocking; /v1/models also triggers it lazily on first call.
    asyncio.create_task(reg.ensure_models_refreshed())
    sched = Scheduler(reg.providers, **cfg.get("scheduler", {}))

    # Token Manager + encrypted vault (optional but recommended)
    tokenmgr = None
    vault_cfg = cfg.get("vault")
    if vault_cfg and vault_cfg.get("enabled"):
        vault = Vault(
            backend=vault_cfg.get("backend", "auto"),
            vault_path=vault_cfg.get("path"),
            keyfile=vault_cfg.get("keyfile"),
        )
        tokenmgr = TokenManager(
            reg.providers,
            vault=vault,
            preempt_sec=vault_cfg.get("preempt_sec", 300),
            refresh_interval=vault_cfg.get("refresh_interval", 120),
            persist=vault_cfg.get("persist", False),
        )
        tokenmgr.restore()
        tokenmgr.start()
        sched.token_manager = tokenmgr

    enabled = list(reg.providers.keys())
    logger.info("aigw %s ready; providers=%s", __version__, enabled)

    STATE.update(
        cfg=cfg,
        http=http,
        reg=reg,
        sched=sched,
        api_key=_declared_api_key(cfg),
        tokenmgr=tokenmgr,
    )


@app.on_event("shutdown")
async def _shutdown():
    if STATE.get("tokenmgr"):
        with contextlib.suppress(Exception):
            await STATE["tokenmgr"].stop()
    with contextlib.suppress(Exception):
        await STATE["http"].aclose()


def _auth(authorization: str | None):
    expected = STATE["api_key"]
    if not authorization or authorization.removeprefix("Bearer ").strip() != expected:
        raise HTTPException(401, "invalid api key")


def _session_id(req: dict, headers) -> str | None:
    """Sticky-session key.

    Prefer an explicit ``x-session-id`` (or body ``session_id``) so multi-turn
    conversations pin to one upstream account. Hash-of-first-messages is only a
    last-resort fallback and can collide across unrelated chats with the same
    opening prompt — callers should always send the header.
    """
    if sid := headers.get("x-session-id"):
        return sid
    if sid := req.get("session_id"):
        if isinstance(sid, str) and sid.strip():
            return sid.strip()
    msgs = req.get("messages", [])
    seed = "".join(
        m.get("content", "") if isinstance(m.get("content"), str) else "" for m in msgs[:2]
    )
    if not seed:
        return None
    sid = hashlib.sha1(seed.encode()).hexdigest()[:16]
    logger.warning(
        "no x-session-id header; sticky session falling back to message-hash %s "
        "(send x-session-id from the client for reliable multi-turn pinning)",
        sid,
    )
    return sid


def _provider_names(provider) -> list[str]:
    if isinstance(provider, (list, tuple)):
        return list(provider)
    return [provider]


def _any_provider_supports(provider, flag: str) -> bool:
    reg: Registry = STATE["reg"]
    for name in _provider_names(provider):
        prov = reg.providers.get(name)
        if not prov:
            continue
        caps = prov.capabilities()
        if getattr(caps, flag, False):
            return True
    return False


@app.get("/v1/models")
async def models(authorization: str = Header(None)):
    _auth(authorization)
    # Ensure the catalog is live (no-op once refreshed). The Antigravity adapter
    # replaces its seed model list with the real one fetched from upstream.
    await STATE["reg"].ensure_models_refreshed()
    return {"object": "list", "data": STATE["reg"].list_models()}


@app.get("/healthz")
async def healthz():
    out: dict = {
        "ok": True,
        "version": __version__,
        "providers": {},
    }
    for name, prov in STATE["reg"].providers.items():
        caps = prov.capabilities().as_dict()
        out["providers"][name] = {
            "capabilities": caps,
            "accounts": [
                {"id": a.id, "state": a.state.value, "fail": a.fail_count, "quota": a.quota}
                for a in prov.accounts
            ],
        }
        # Back-compat: top-level provider name -> account list (old clients)
        out[name] = out["providers"][name]["accounts"]
    if STATE.get("tokenmgr"):
        out["tokens"] = STATE["tokenmgr"].status()
    return out


@app.post("/v1/chat/completions")
async def chat_completions(request: Request, authorization: str = Header(None)):
    _auth(authorization)
    body = await request.json()
    if "model" not in body or "messages" not in body:
        raise HTTPException(400, "model and messages are required")

    try:
        provider, canonical = STATE["reg"].resolve(body["model"])
    except KeyError as e:
        raise HTTPException(404, str(e))
    body["model"] = canonical

    # Fail fast when the client asks for features the route cannot provide.
    wants_tools = bool(body.get("tools") or body.get("functions"))
    if wants_tools and not _any_provider_supports(provider, "tools"):
        raise HTTPException(
            400,
            f"model '{canonical}' route does not support tools/tool_calls "
            f"(provider capabilities.tools=false). Pick a tools-capable model "
            f"or drop the tools field.",
        )

    sid = _session_id(body, request.headers)
    stream = bool(body.get("stream"))
    if stream and not _any_provider_supports(provider, "stream"):
        raise HTTPException(
            400,
            f"model '{canonical}' route does not support stream=true",
        )

    sched: Scheduler = STATE["sched"]

    try:
        if stream:
            gen = await sched.dispatch(provider, body, sid, stream=True)
            return StreamingResponse(_sse(gen), media_type="text/event-stream")
        result = await sched.dispatch(provider, body, sid, stream=False)
        return JSONResponse(result)
    except NoAccountAvailable as e:
        raise HTTPException(503, f"no upstream account available: {e}")
    except UpstreamError as e:
        raise HTTPException(e.status if 400 <= e.status < 600 else 502, str(e))


async def _sse(gen):
    try:
        async for chunk in gen:
            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
    except UpstreamError as e:
        err = {"error": {"message": str(e), "type": "upstream_error", "code": e.status}}
        yield f"data: {json.dumps(err)}\n\n"
    yield "data: [DONE]\n\n"


@app.post("/v1/embeddings")
async def embeddings(request: Request, authorization: str = Header(None)):
    _auth(authorization)
    body = await request.json()
    if "input" not in body:
        raise HTTPException(400, "input is required")
    model = body.get("model", "mock/embedding")
    try:
        provider, canonical = STATE["reg"].resolve(model)
    except KeyError as e:
        raise HTTPException(404, str(e))
    body["model"] = canonical
    if not _any_provider_supports(provider, "embeddings"):
        raise HTTPException(
            501,
            f"model '{canonical}' route does not support embeddings",
        )
    prov_name = provider[0] if isinstance(provider, (list, tuple)) else provider
    prov = STATE["reg"].providers.get(prov_name)
    if prov is None:
        raise HTTPException(503, f"no provider for '{prov_name}'")
    acc = next((a for a in prov.accounts if a.schedulable()), None)
    if acc is None:
        raise HTTPException(503, "no upstream account available")
    try:
        result = await prov.embeddings(acc, body)
        return JSONResponse(result)
    except UpstreamError as e:
        raise HTTPException(e.status if 400 <= e.status < 600 else 502, str(e))
