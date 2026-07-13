"""OpenAI-compatible HTTP surface.

Endpoints:
  GET  /v1/models
  POST /v1/chat/completions   (stream + non-stream)
  POST /v1/embeddings
  GET  /healthz               (pool health + token health)

Downstream auth: a single local API key (sk-...) so only your own software can call
this gateway. Upstream account selection / token refresh is handled by the Scheduler
and TokenManager.
"""
from __future__ import annotations

import json
import time
import hashlib
import contextlib
import logging

import httpx
from fastapi import FastAPI, Request, HTTPException, Header
from fastapi.responses import StreamingResponse, JSONResponse

from .config import load
from .registry import Registry
from .scheduler import Scheduler, NoAccountAvailable
from .providers.base import UpstreamError
from .tokens.vault import Vault
from .tokens.manager import TokenManager

logger = logging.getLogger("aigw")


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
        format=cfg.get("format",
                       "%(asctime)s %(levelname)-7s %(name)s: %(message)s"),
        handlers=handlers,
        force=True,
    )


app = FastAPI(title="aigw — unified desktop-quota gateway")
STATE: dict = {}


@app.on_event("startup")
async def _startup():
    cfg = load()
    setup_logging(cfg.get("logging"))
    # trust_env defaults to True so the gateway honours HTTP(S)_PROXY in the
    # environment (e.g. a local V2rayU / Clash proxy). Set trust_env: false in
    # config only if you specifically need direct egress.
    http = httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0),
                             http2=True, trust_env=cfg.get("trust_env", True))
    reg = await Registry(cfg, http).build()
    sched = Scheduler(reg.providers, **cfg.get("scheduler", {}))

    # Token Manager + encrypted vault (optional but recommended)
    tokenmgr = None
    vault_cfg = cfg.get("vault")
    if vault_cfg and vault_cfg.get("enabled"):
        vault = Vault(backend=vault_cfg.get("backend", "auto"),
                      vault_path=vault_cfg.get("path"),
                      keyfile=vault_cfg.get("keyfile"))
        tokenmgr = TokenManager(
            reg.providers, vault=vault,
            preempt_sec=vault_cfg.get("preempt_sec", 300),
            refresh_interval=vault_cfg.get("refresh_interval", 120),
            persist=vault_cfg.get("persist", False),
        )
        tokenmgr.restore()
        tokenmgr.start()
        sched.token_manager = tokenmgr

    STATE.update(cfg=cfg, http=http, reg=reg, sched=sched,
                 api_key=cfg["server"]["api_key"], tokenmgr=tokenmgr)


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
    """Sticky-session key: explicit header wins, else hash of the system+first msg
    so a multi-turn conversation keeps landing on the same upstream account."""
    if sid := headers.get("x-session-id"):
        return sid
    msgs = req.get("messages", [])
    seed = "".join(m.get("content", "") if isinstance(m.get("content"), str) else ""
                   for m in msgs[:2])
    return hashlib.sha1(seed.encode()).hexdigest()[:16] if seed else None


@app.get("/v1/models")
async def models(authorization: str = Header(None)):
    _auth(authorization)
    return {"object": "list", "data": STATE["reg"].list_models()}


@app.get("/healthz")
async def healthz():
    out = {}
    for name, prov in STATE["reg"].providers.items():
        out[name] = [{"id": a.id, "state": a.state.value,
                      "fail": a.fail_count, "quota": a.quota} for a in prov.accounts]
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
    sid = _session_id(body, request.headers)
    stream = bool(body.get("stream"))
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
