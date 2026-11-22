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
import os

import httpx
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from . import __version__
from .config import load
from . import authflow
from . import registry as registry_mod
from .providers.base import UpstreamError
from .providers.apps.antigravity.network import probe_network
from .registry import Registry
from .scheduler import NoAccountAvailable, Scheduler
from .token_refresh_service import TokenRefreshService
from .tokens.manager import TokenManager
from .tokens.vault import Vault

logger = logging.getLogger("bezel")

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
            "bezel: server.api_key is not declared in the gateway config. This service "
            "owns that value and carries no built-in fallback — set it (or "
            "${BEZEL_KEY:-…} in the YAML) before starting the hub."
        )
    key = str(server.get("api_key") or "").strip()
    if not key:
        raise RuntimeError(
            "bezel: server.api_key is declared empty. An empty string means 'not "
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


app = FastAPI(title="bezel — unified desktop-quota gateway", version=__version__)
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
            "server.host=%s exposes bezel on all interfaces — keep a strong api_key "
            "and prefer a reverse proxy; do not rely on obscurity.",
            host,
        )
    if not key or key in _WEAK_API_KEYS or key.startswith("sk-local-"):
        logger.warning(
            "weak/default api_key in use (%r). Fine for mock/local demos; set "
            "BEZEL_KEY to a high-entropy secret before pointing real clients at bezel.",
            key[:16] + ("…" if len(key) > 16 else ""),
        )


QUOTA_REFRESH_SECONDS = 900.0

#: How long one `/v1/models` read may wait for the model refresh before answering with
#: what is known. Spawn-based legs start their own process before they can name anything
#: (cline ~10 s, qwen ~25 s on this machine, 2026-10-29) — a desktop that opened during
#: those seconds must not see a request that never comes back.
MODEL_CATALOG_WAIT = 4.0


async def _quota_refresh_loop(reg, interval: float) -> None:
    """Keep account quota snapshots fresh without blocking chat routing."""
    await reg.ensure_models_refreshed()
    while True:
        if any(prov.accounts for prov in reg.providers.values()):
            network = await asyncio.to_thread(probe_network)
            STATE["network"] = network
            if not network["ok"]:
                # The probe names Google hosts (the Antigravity OAuth path). Other
                # legs have their own endpoints: Claude/Codex/OpenCode may still be
                # reachable while Google is not, so a failed Google probe must not
                # skip the whole pool. Each provider keeps its last snapshot on its
                # own failure (see refresh_quota).
                logger.warning(
                    "Google endpoints unreachable (%s); other quota legs keep their own "
                    "endpoint checks. %s",
                    ", ".join(network.get("failed") or []), network.get("advice", ""),
                )
        try:
            # A leg whose store was locked or mid-renewal at boot has no seat, and only a
            # later local read can give it one (installed 0.3.0, 2026-11-08: Kiro read fine
            # a minute after boot while the hub still had zero seats). Cheap, local, retry.
            await reg.ensure_accounts_discovered()
            # A leg whose catalog never came back (node down at boot) has no model to
            # offer, and only an upstream answer can fix that. The picker re-asks when it
            # polls; this catches the case where nobody opens it.
            await reg.ensure_models_refreshed()
            await reg.refresh_quotas()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("quota refresh loop failed: %s: %s", type(exc).__name__, exc)
        await asyncio.sleep(interval)


@app.on_event("startup")
async def _startup():
    cfg = load()
    setup_logging(cfg.get("logging"))
    _startup_safety_checks(cfg)
    # trust_env defaults to True so the gateway honours HTTP(S)_PROXY in the
    # environment (e.g. a local V2rayU / Clash proxy). Set trust_env: false in
    # config only if you specifically need direct egress.
    #
    # http2 is best-effort: httpx raises ImportError at *client construction*
    # when the h2/hpack/hyperframe stack is missing. bezel declares
    # `httpx[http2]`, but the desktop's installed backend venv is built from
    # plobi-agent's own lock closure, which never contained that extra — the
    # gateway used to die at startup with a bare ImportError that the desktop
    # could only report as a 20s "gateway did not become healthy in time"
    # timeout. Losing the whole quota hub to a missing optional extra is not an
    # acceptable failure mode: fall back to HTTP/1.1 and say so once.
    # (plobi-agent carries an `bezel = ["h2==..."]` extra so the installed venv
    # does have the stack; this is the belt to that suspenders.)
    try:
        http = httpx.AsyncClient(
            timeout=httpx.Timeout(120.0, connect=10.0), http2=True, trust_env=cfg.get("trust_env", True)
        )
    except ImportError:
        logger.warning(
            "httpx http2 extra missing (h2 not installed) — falling back to HTTP/1.1; "
            "install with `uv pip install 'httpx[http2]'`"
        )
        http = httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0), trust_env=cfg.get("trust_env", True))
    reg = await Registry(cfg, http).build()
    # Eagerly refresh each provider's model catalog from upstream (Antigravity
    # fetches its real model list via fetchAvailableModels). Best-effort and
    # non-blocking; /v1/models also waits on it, but only briefly (see
    # MODEL_CATALOG_WAIT).
    reg.models_refresh_task()
    quota_task = asyncio.create_task(
        _quota_refresh_loop(reg, float(cfg.get("quota_refresh_seconds", QUOTA_REFRESH_SECONDS)))
    )
    sched = Scheduler(reg.providers, **cfg.get("scheduler", {}))

    # Background token-renewal worker (sub2api style). Off unless the operator opts in:
    # BEZEL_TOKEN_REFRESH=1, or `token_refresh: {enabled: true}` in the config.
    # It sweeps on a timer and calls the same measured ensure_fresh paths the scheduler
    # would, so leases renew with 24 h of runway instead of at the last second.
    refresh_task = None
    refresh_cfg = cfg.get("token_refresh") or {}
    if os.environ.get("BEZEL_TOKEN_REFRESH", "").strip() or refresh_cfg.get("enabled"):
        refresh_svc = TokenRefreshService(
            reg.providers,
            interval_seconds=float(refresh_cfg.get("interval_seconds", 300)),
            skew_seconds=float(refresh_cfg.get("skew_seconds", 24 * 3600)),
        )
        refresh_task = asyncio.create_task(refresh_svc.run())
        logger.info("token refresh worker started (BEZEL_TOKEN_REFRESH=1)")

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
    logger.info("bezel %s ready; providers=%s", __version__, enabled)

    STATE.update(
        cfg=cfg,
        http=http,
        reg=reg,
        sched=sched,
        api_key=_declared_api_key(cfg),
        tokenmgr=tokenmgr,
        quota_task=quota_task,
        refresh_task=refresh_task,
    )


@app.on_event("shutdown")
async def _shutdown():
    if STATE.get("quota_task"):
        with contextlib.suppress(Exception):
            STATE["quota_task"].cancel()
    if STATE.get("refresh_task"):
        with contextlib.suppress(Exception):
            STATE["refresh_task"].cancel()
    if STATE.get("tokenmgr"):
        with contextlib.suppress(Exception):
            await STATE["tokenmgr"].stop()
    # Close every leg that holds a child process (an ACP session is a live `cline`/`qwen`
    # node process). The desktop reaps the whole tree with `taskkill /T` on quit, but a
    # graceful stop — Ctrl+C in dev, a test teardown — runs this handler and nothing else.
    reg = STATE.get("reg")
    if reg is not None:
        for prov in list(reg.providers.values()):
            close = getattr(prov, "aclose", None)
            if callable(close):
                with contextlib.suppress(Exception):
                    await close()
    with contextlib.suppress(Exception):
        await STATE["http"].aclose()


def _auth(authorization: str | None):
    expected = STATE["api_key"]
    if not authorization or authorization.removeprefix("Bearer ").strip() != expected:
        raise HTTPException(401, "invalid api key")


async def _json_body(request: Request) -> dict:
    """Parse a JSON request body, or answer 400.

    `await request.json()` raises the decoder's own exception, which reaches the
    caller as a bare `500 Internal Server Error` — a wrong-body problem dressed up
    as our failure. This is a system boundary (any local process can POST here), so
    the client's mistake gets the client's status code and a sentence.
    """
    try:
        return await request.json()
    except Exception as e:  # json.JSONDecodeError, UnicodeDecodeError, …
        raise HTTPException(400, f"request body is not valid JSON ({type(e).__name__})") from e


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


#: Why a leg has no schedulable account, in words an error card can show.
#: Measured 2026-10-29: with a credential present and the Settings → VPN node down, a turn
#: answered `503 no upstream account available: no more accounts for ['antigravity']` after
#: 11 s — English, and it reads like a config problem instead of "the node is off".
_NO_ACCOUNT_WORDS = {
    "no_proxy": "还没配 VPN 节点，这条渠道连不上",
    "proxy_unreachable": "VPN 节点没应答——把它起起来再试",
    "credential_expired": "登录凭据已过期，打开那个应用一次就能续上",
    "no_desktop_credential": "本机未读取到它的登录凭据",
    "not_signed_in": "那边还没登录",
    "not_probed": "还没问过它的登录状态，稍等一下再发",
    # 10-29 23:5x 实测：凭据好的、节点也是好的，上游对每一个模型都回 429
    # RESOURCE_EXHAUSTED —— 是那个账号的额度用完了，既不是没登录也不是线路问题。
    "quota_exhausted": "那个账号的额度用完了，等它恢复再试",
}


def _no_account_detail(prov) -> str:
    """The provider's own answer to "why can't this leg serve right now", in Chinese."""
    if prov is None:
        return ""
    try:
        live, reason = prov.credential_health()
    except Exception:  # noqa: BLE001 — a probe crash must not mask the original 503
        return ""
    if live:
        # Everything local says the leg is fine and it still has no schedulable account
        # (a stale lease the owner could not re-push, an account the upstream just
        # retired). That is not English's job to explain.
        return "这条腿现在没有一个能服务回合的账号——通常是它的登录租约刚过期，稍后再发"
    return _NO_ACCOUNT_WORDS.get(str(reason), "") or (str(reason) if reason and not str(reason).isascii() else "")


def _quota_retry_after(prov) -> int | None:
    """How many seconds a *cooling* leg wants the caller to wait; None if it isn't cooling.

    Measured 2026-11-05 on the opencode free pool: the upstream answered 429
    FreeUsageLimitError with retry-after about 76331s, and the leg parked itself for
    that long, refusing every later request in 4-96ms without touching the network.
    That is not a service outage, it is a spent allowance with a known end - so the
    remaining seconds belong in a header, not in an undated 503.
    """
    if prov is None:
        return None
    probe = getattr(prov, "quota_retry_after", None)
    if not callable(probe):
        return None
    try:
        wait = probe()
    except Exception:  # a probe failing on the error path must not mask the real refusal
        return None
    try:
        secs = int(wait)
    except (TypeError, ValueError):
        return None
    return secs if secs > 0 else None


@app.get("/v1/models")
async def models(
    authorization: str = Header(None),
    selectable: bool = Query(False),
):
    _auth(authorization)
    # Read the catalog without waiting on a leg that is still booting. The refresh runs
    # as one shared task; this request only waits for it briefly and then answers with
    # what is known (measured 2026-10-29: cline ~10 s, qwen ~25 s to answer at all).
    try:
        await asyncio.wait_for(asyncio.shield(STATE["reg"].models_refresh_task()), timeout=MODEL_CATALOG_WAIT)
    except asyncio.TimeoutError:
        pass
    # The OpenAI-compat catalog stays complete: a consumer that only wants models
    # that can serve an agent turn (tool-capable AND live credential) asks
    # `?selectable=1`. Every row also carries its own `selectable` +
    # `unselectable_reason`, so a missing model can be explained instead of guessed
    # at from the client side.
    return {"object": "list", "data": STATE["reg"].list_models(selectable_only=selectable)}


@app.get("/v1/models/{model_id:path}")
async def model_detail(
    model_id: str,
    authorization: str = Header(None),
):
    _auth(authorization)
    try:
        await asyncio.wait_for(asyncio.shield(STATE["reg"].models_refresh_task()), timeout=MODEL_CATALOG_WAIT)
    except asyncio.TimeoutError:
        pass
    reg: Registry = STATE["reg"]
    for row in reg.list_models(selectable_only=False):
        if row["id"] == model_id:
            return row
    try:
        prov_name, canonical = reg.resolve(model_id)
    except KeyError as e:
        raise HTTPException(404, str(e)) from e
    if isinstance(prov_name, (list, tuple)):
        prov_name = prov_name[0]
    return {
        "id": canonical,
        "object": "model",
        "created": 0,
        "owned_by": "bezel",
        "provider": prov_name,
    }


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
    if STATE.get("network"):
        out["network"] = STATE["network"]
    return out


# ── click-to-browser sign-in ─────────────────────────────────────────────
# The desktop's 连接 button asks HERE instead of carrying a list of product names:
# `GET /plobi/auth` is the catalog of legs a click can sign into (with each one's
# live verdict), `POST /plobi/auth/{app}` starts the flow, and `GET` on the same path
# is what the button polls until the leg comes back. The flows themselves run in the
# providers' own hands — a CLI's login command or the Antigravity PKCE path — because
# the rule of this whole layer is that the credential belongs to the desktop app, and
# Plobi never reads, copies, prints or persists it.
@app.get("/plobi/auth")
async def plobi_auth_catalog(authorization: str = Header(None)):
    _auth(authorization)
    reg = STATE.get("reg")
    rows = authflow.auth_catalog(reg)
    live = authflow.MANAGER.status()
    for row in rows:
        row["login"] = live.get(row["id"])
    return {"object": "list", "data": rows}


async def _recheck_after_login(app_id: str) -> None:
    """After a sign-in, let the leg re-discover itself and refresh its catalog."""
    reg = STATE.get("reg")
    if reg is None:
        return
    prov = reg.providers.get(app_id)
    if prov is None:
        return
    try:
        await prov.discover_accounts()
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("bezel").warning(
            "post-login discovery failed for %s: %s: %s", app_id, type(exc).__name__, exc
        )
        return
    acc = next((a for a in prov.accounts if a.schedulable()), None)
    if acc is None:
        return
    # `credential_health` — and therefore the catalog gate — reads the leg's own probe
    # verdict, and that verdict is cached behind a TTL. Signing in is exactly the event
    # that makes the cache wrong, so re-read it instead of trusting it.
    probe = getattr(prov, "probe", None)
    if callable(probe):
        try:
            await probe(force=True)
        except Exception:  # noqa: BLE001
            pass
    try:
        # Through the gate, not around it: ensure_models_refreshed re-reads the credential
        # /seat signature on entry and the re-probe above just moved it, so the passes this
        # registry burned while the leg was signed out are discarded — and every leg the
        # same outage left empty gets another chance, not only this one.
        await reg.ensure_models_refreshed()
        await prov.refresh_quota(acc)
    except Exception:  # noqa: BLE001
        pass


@app.post("/plobi/auth/{app_id}")
async def plobi_auth_start(app_id: str, authorization: str = Header(None)):
    _auth(authorization)
    reg = STATE.get("reg")
    # 2026-11-07（用户）：屏幕上的 + 号对每一条已适配的腿都成立，包括 config.yaml
    # 没有预启用的那些。所以点下去的第一件事是按需把这条腿装进 registry ——
    # 否则「有卡片、点不动」就是死路。
    if reg is not None and app_id not in reg.providers:
        await reg.enable_leg(app_id)
    plan = authflow.login_plan_for(reg, app_id)
    plan["on_success"] = lambda: _recheck_after_login(app_id)
    session = await authflow.MANAGER.start(app_id, plan, settle=2.0)
    return session.as_dict()


@app.get("/plobi/auth/{app_id}")
async def plobi_auth_status(app_id: str, authorization: str = Header(None)):
    _auth(authorization)
    session = authflow.MANAGER.get(app_id)
    reg = STATE.get("reg")
    plan = authflow.login_plan_for(reg, app_id)
    prov = reg.providers.get(app_id) if reg else None
    live, reason = reg.credential_health(prov) if (reg and prov) else (
        bool(authflow._resolve_binary(plan.get("binary", ""))),
        "已装，待本人登录" if authflow._resolve_binary(plan.get("binary", "")) else "这台机器上还没装它的 CLI",
    )
    return {
        "app": app_id,
        "name": plan.get("name") or app_id,
        "kind": plan.get("kind", "none"),
        "live": bool(live),
        "reason": reason,
        "login": session.as_dict() if session else None,
    }


@app.post("/plobi/install/{app_id}")
async def plobi_install(app_id: str, authorization: str = Header(None)):
    """装一条 CLI 腿 —— 命令只来自清单，调用方不能自己带命令进来。

    2026-11-07（用户）：首次配置里「未安装的 CLI 类给出自动安装动作」。可执行的东西
    必须只有一个来源：`ADAPTED_APPS[*].install.command`。请求体不参与，前端点的是
    「装这一条」，不是「跑这一句」——否则这就是一个万能命令执行口。
    """
    _auth(authorization)
    spec = registry_mod.spec_for(app_id)
    install = spec.get("install") or {}
    command = install.get("command")

    if not command:
        # 订阅类腿的安装命令是运行时才知道的：插件 spec 由那条腿自己报出来，静态
        # 清单里没有。仍然只信我们后端拼出来的 catalog —— 调用方还是不能自带命令。
        row = next(
            (r for r in authflow.auth_catalog(STATE.get("reg")) if r.get("id") == app_id),
            None,
        )
        command = (row or {}).get("install_command") or None

    if not command:
        return {
            "ok": False,
            "code": "NO_COMMAND",
            "detail": "这条腿没有可自动执行的安装命令，请用官方安装包",
            "download": spec.get("download"),
        }

    logger.info("installing leg %s via %s", app_id, install.get("kind"))

    try:
        proc = await asyncio.create_subprocess_shell(
            str(command),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        raw, _ = await asyncio.wait_for(proc.communicate(), timeout=600)
    except asyncio.TimeoutError:
        return {"ok": False, "code": "TIMEOUT", "detail": "安装超时（10 分钟）"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "code": "SPAWN_FAILED", "detail": f"{type(exc).__name__}: {exc}"}

    output = (raw or b"").decode("utf-8", "replace")
    installed = registry_mod.detect_installed(spec)

    reg = STATE.get("reg")
    if reg is not None:
        # 刚装上的插件不能还被上一轮缓存挡着。订阅类腿的「装没装」是插件在不在，
        # 静态判据答不了这一问，所以先失效缓存，装完再问一遍 catalog。
        for leg in list(reg.providers.values()):
            if hasattr(leg, "_installed_plugins_cache"):
                with contextlib.suppress(Exception):
                    leg._installed_plugins_cache = None
        if not installed:
            row = next(
                (r for r in authflow.auth_catalog(reg) if r.get("id") == app_id),
                None,
            )
            installed = bool((row or {}).get("installed"))
    if installed and reg is not None:
        with contextlib.suppress(Exception):
            prov = await reg.enable_leg(app_id)
            if prov is not None:
                await prov.discover_accounts()

    return {
        "ok": bool(installed),
        "code": "INSTALLED" if installed else "STILL_MISSING",
        "installed": bool(installed),
        # 只回最后一段日志：装没装成由 installed 说话，日志是给人排查用的。
        "tail": output[-2000:],
        "exit_code": proc.returncode,
    }


@app.post("/v1/chat/completions")
async def chat_completions(request: Request, authorization: str = Header(None)):
    _auth(authorization)
    body = await _json_body(request)
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
        # 裁定 152 那一族的判据（用户那句「日程排的根本无法使用」压在这条上）：
        # **上游回 200 而一个字都没有，不是"答完了"，是"没货"**。以前这里直接 JSONResponse 出去，
        # 调用方拿到 200 + 空 content，只能猜——猜错就成了"这条腿活着但什么都排不出来"。
        # 有 tool_calls 的空 content 是合法形状（那一轮本来就不该有正文），所以不是一刀切看 content。
        if _is_no_goods(result):
            raise HTTPException(
                502,
                "upstream returned an empty completion (没货：200 而 content 为空、也没有 tool_calls) "
                f"— finish_reason={_finish_reason(result)!r}",
            )
        return JSONResponse(result)
    except NoAccountAvailable as e:
        reg = STATE.get("reg")
        names = list(provider) if isinstance(provider, (list, tuple)) else [provider]
        prov0 = next(
            (reg.providers.get(n) for n in names if reg is not None and reg.providers.get(n) is not None),
            None,
        )
        # A leg that is merely *spent* is not a leg that is *down*. 503 tells the client
        # "the service is unavailable" and clients answer it by retrying at once — which
        # is exactly what re-armed the upstream limit on 2026-11-05 (refusals came back
        # in 4-96 ms, never reaching the network). 429 + Retry-After states the truth:
        # this account has spent its allowance, come back in N seconds.
        _wait = _quota_retry_after(prov0)
        _detail = _no_account_detail(prov0) or f"no upstream account available: {e}"
        if _wait is not None:
            raise HTTPException(429, _detail, headers={"Retry-After": str(_wait)})
        # Not "spent" (that is the 429 above) but "cooling": the leg says when it can
        # serve again, so say it. A 503 with no Retry-After is a client left guessing,
        # and on 2026-11-14 the guess was 3 attempts in 9 seconds against a 3 m 46 s gap.
        _soon = getattr(e, "retry_after", None)
        if _soon:
            raise HTTPException(
                503, _detail, headers={"Retry-After": str(max(1, int(round(_soon))))}
            )
        raise HTTPException(503, _detail)
    except UpstreamError as e:
        status = e.status if 400 <= e.status < 600 else 502
        # 一条腿"还没准备好"不是"网关坏了"。上游是本地侧车时，重启窗口内它会回
        # 502/503/504 —— 原样透传出去，客户端读到的是"坏网关"，于是连着重试
        # （实测 2026-11-14 19:51：3 次全在 134 ms 内砸回来，总跨度 9 秒，而空窗
        # 是 3 分 46 秒）。503 + Retry-After 才是实情：等一会儿再来。
        headers = None
        if status == 503 and e.retryable:
            headers = {"Retry-After": str(max(1, int(round(e.cooldown or 5.0))))}
        raise HTTPException(status, str(e), headers=headers)


async def _sse(gen):
    try:
        async for chunk in gen:
            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
    except UpstreamError as e:
        err = {"error": {"message": str(e), "type": "upstream_error", "code": e.status}}
        yield f"data: {json.dumps(err)}\n\n"
    yield "data: [DONE]\n\n"


#: finish reasons that prove the upstream did a turn, even when the visible content
#: is empty. Only `length` belongs here: the model ran and hit the output budget
#: (`stop` with nothing at all is still the "no goods" case — see `_is_no_goods`).
_NOT_EMPTY_FINISH_REASONS = frozenset({"length"})


def _first_choice(result: object) -> dict:
    if not isinstance(result, dict):
        return {}
    choices = result.get("choices") or []
    return choices[0] if choices and isinstance(choices[0], dict) else {}


def _is_no_goods(result: object) -> bool:
    """这一发是不是"没货"：没有正文、没有 tool_calls、也没有思考。"""
    choice = _first_choice(result)
    message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
    content = message.get("content")
    text = content if isinstance(content, str) else ""
    if text.strip():
        return False
    if message.get("tool_calls") or choice.get("tool_calls"):
        return False
    # 思考也是货。reasoning 模型把预算花在 reasoning 上时 content 是空的（`null`），
    # 那一轮的正文本来就该空着——把它判成"没货"，等于把上游真烧掉的 token 说成上游
    # 的空话；客户端再把 502 当网关故障重试，同一个请求于是再烧一遍预算
    # （实测 2026-11-14：`workbuddy/hy3` + `max_tokens=8`，200 而 content=null、
    # reasoning_content 有字、finish_reason='length'，被这里判成了 502）。
    for key in ("reasoning_content", "reasoning"):
        thinking = message.get(key)
        if isinstance(thinking, str) and thinking.strip():
            return False
    # 被预算截断也是干过活的证据：上游跑完了一轮才撞上 length。
    if _finish_reason(result) in _NOT_EMPTY_FINISH_REASONS:
        return False
    return True


def _finish_reason(result: object) -> str:
    return str(_first_choice(result).get("finish_reason") or "")


@app.post("/v1/embeddings")
async def embeddings(request: Request, authorization: str = Header(None)):
    _auth(authorization)
    body = await _json_body(request)
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
        raise HTTPException(503, _no_account_detail(prov) or "no upstream account available")
    try:
        result = await prov.embeddings(acc, body)
        return JSONResponse(result)
    except UpstreamError as e:
        raise HTTPException(e.status if 400 <= e.status < 600 else 502, str(e))
