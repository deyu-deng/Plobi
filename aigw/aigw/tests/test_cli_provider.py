"""Verification for the spawn-based CLI providers (opendesign-style).

Uses a fake `agy` script (tests/fake_cli.py) so the FULL spawn -> capture ->
OpenAI-translation pipeline is exercised WITHOUT needing the real CLI installed
or any vendor account.

Run:
    cd Code/aigw && python -m aigw.tests.test_cli_provider
    # or: pytest aigw/tests/test_cli_provider.py -v
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx

# make `aigw` importable when run as a script from within Code/aigw
_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import aigw.main as main
from aigw.providers.antigravity_cli import AntigravityCliProvider
from aigw.providers.base import UpstreamError
from aigw.providers.marvis_cli import MarvisCliProvider
from aigw.providers.marvis_gui import MarvisGuiProvider
from aigw.providers.workbuddy_cli import WorkbuddyCliProvider
from aigw.providers.workbuddy_gui import WorkbuddyGuiProvider
from aigw.providers.workbuddy_hybrid import WorkbuddyHybridProvider
from aigw.registry import Registry
from aigw.scheduler import Scheduler

FAKE = str(Path(__file__).resolve().parent / "fake_cli.py")
PY = sys.executable


def _cli_cfg(models, model_map, **extra):
    cfg = {
        "binary": FAKE,
        "interpreter": PY,
        "prompt_flag": "-p",
        "model_flag": "--model",
        "models": models,
        "model_map": model_map,
        "accounts": [{"id": "cli-1"}],
    }
    cfg.update(extra)
    return cfg


def test_binary_missing_disables():
    prov = AntigravityCliProvider(
        {"binary": "definitely-not-a-real-binary-xyz", "models": ["x/y"], "accounts": [{}]}, None
    )
    accs = asyncio.run(prov.discover_accounts())
    assert accs == [], "missing binary must yield zero accounts"
    print("  ok: missing binary -> 0 accounts (graceful disable)")


def test_chat_captures_stdout():
    prov = AntigravityCliProvider(
        _cli_cfg(
            ["antigravity_cli/gemini-3-pro"], {"antigravity_cli/gemini-3-pro": "gemini-3-pro"}
        ),
        None,
    )
    asyncio.run(prov.discover_accounts())
    assert len(prov.accounts) == 1, "one account expected when CLI present"
    req = {
        "model": "antigravity_cli/gemini-3-pro",
        "messages": [{"role": "user", "content": "hello world"}],
    }
    out = asyncio.run(prov.chat(prov.accounts[0], req))
    assert out["object"] == "chat.completion"
    assert "hello world" in out["choices"][0]["message"]["content"]
    assert out["choices"][0]["message"]["content"].startswith("FAKE_CLI<<")
    print("  ok: chat() captures CLI stdout and maps to OpenAI shape")


def test_stream_yields_chunks():
    prov = AntigravityCliProvider(
        _cli_cfg(
            ["antigravity_cli/gemini-3-pro"], {"antigravity_cli/gemini-3-pro": "gemini-3-pro"}
        ),
        None,
    )
    asyncio.run(prov.discover_accounts())
    req = {
        "model": "antigravity_cli/gemini-3-pro",
        "messages": [{"role": "user", "content": "stream me"}],
    }
    chunks = []

    async def _collect():
        async for c in prov.chat_stream(prov.accounts[0], req):
            chunks.append(c)

    asyncio.run(_collect())
    content = "".join(
        c["choices"][0]["delta"].get("content", "")
        for c in chunks
        if "content" in c["choices"][0]["delta"]
    )
    assert "stream me" in content
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"
    print("  ok: chat_stream() yields content chunks then stop")


def test_e2e_through_gateway():
    cfg = {
        "server": {"api_key": "sk-test"},
        "providers": {
            "antigravity_cli": _cli_cfg(
                ["antigravity_cli/gemini-3-pro"], {"antigravity_cli/gemini-3-pro": "gemini-3-pro"}
            ),
        },
        "routing": {"rules": []},
    }
    asyncio.run(_e2e(cfg))
    print("  ok: e2e through gateway returns 200 with CLI output")


async def _e2e(cfg):
    dummy = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json={}))
    )
    reg = await Registry(cfg, dummy).build()
    sched = Scheduler(reg.providers, **cfg.get("scheduler", {}))
    # ASGITransport does not run lifespan startup, so inject our STATE directly.
    main.STATE.update(
        cfg=cfg, http=dummy, reg=reg, sched=sched, api_key=cfg["server"]["api_key"], tokenmgr=None
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.app), base_url="http://t"
    ) as c:
        r = await c.post(
            "/v1/chat/completions",
            headers={"authorization": "Bearer sk-test"},
            json={
                "model": "antigravity_cli/gemini-3-pro",
                "messages": [{"role": "user", "content": "end to end"}],
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert "end to end" in body["choices"][0]["message"]["content"]


def test_marvis_cli_via_fake():
    # The generic MarvisCliProvider must work for any CLI-based Marvis build.
    prov = MarvisCliProvider(
        _cli_cfg(["marvis_cli/default"], {"marvis_cli/default": "default"}), None
    )
    asyncio.run(prov.discover_accounts())
    assert len(prov.accounts) == 1, "one account expected when CLI present"
    req = {
        "model": "marvis_cli/default",
        "messages": [{"role": "user", "content": "marvis prompt"}],
    }
    out = asyncio.run(prov.chat(prov.accounts[0], req))
    assert out["object"] == "chat.completion"
    assert "marvis prompt" in out["choices"][0]["message"]["content"]
    print("  ok: MarvisCliProvider (generic) works through fake CLI")


def test_marvis_gui_graceful_disable():
    # On a headless machine without uiautomation / a running Marvis window, the
    # GUI provider must disable itself cleanly (0 accounts) instead of crashing.
    prov = MarvisGuiProvider({"models": ["marvis_gui/default"], "accounts": [{"id": "g1"}]}, None)
    accs = asyncio.run(prov.discover_accounts())
    assert accs == [], "GUI provider must disable gracefully when UI/lib absent"
    print("  ok: marvis_gui disables gracefully (no uiautomation / no window)")


def test_workbuddy_cli_via_fake():
    # The generic WorkbuddyCliProvider must work for any CLI-shaped Workbuddy.
    prov = WorkbuddyCliProvider(
        _cli_cfg(["workbuddy/default"], {"workbuddy/default": "default"}), None
    )
    asyncio.run(prov.discover_accounts())
    assert len(prov.accounts) == 1, "one account expected when CLI present"
    req = {
        "model": "workbuddy/default",
        "messages": [{"role": "user", "content": "workbuddy prompt"}],
    }
    out = asyncio.run(prov.chat(prov.accounts[0], req))
    assert out["object"] == "chat.completion"
    assert "workbuddy prompt" in out["choices"][0]["message"]["content"]
    print("  ok: WorkbuddyCliProvider works through fake CLI")


def test_workbuddy_gui_graceful_disable():
    # Same headless guard as marvis_gui.
    prov = WorkbuddyGuiProvider(
        {"models": ["workbuddy_gui/default"], "accounts": [{"id": "g2"}]}, None
    )
    accs = asyncio.run(prov.discover_accounts())
    assert accs == [], "Workbuddy GUI provider must disable gracefully when UI/lib absent"
    print("  ok: workbuddy_gui disables gracefully (no uiautomation / no window)")


def test_workbuddy_hybrid_prefers_cli():
    # Hybrid with a working CLI (fake) and NO gui block -> 1 CLI account, chat works.
    cfg = {
        "prefer": "cli",
        "models": ["workbuddy/default"],
        "cli": _cli_cfg(["workbuddy/default"], {"workbuddy/default": "default"}),
    }
    prov = WorkbuddyHybridProvider(cfg, None)
    accs = asyncio.run(prov.discover_accounts())
    assert len(accs) == 1, "expected exactly one CLI account (gui absent)"
    assert accs[0].id.endswith("-cli"), f"account id must be CLI-namespaced: {accs[0].id}"
    req = {"model": "workbuddy/default", "messages": [{"role": "user", "content": "hybrid cli"}]}
    out = asyncio.run(prov.chat(accs[0], req))
    assert "hybrid cli" in out["choices"][0]["message"]["content"]
    print("  ok: WorkbuddyHybrid prefers CLI and serves chat")


def test_workbuddy_hybrid_disables_without_cli_or_gui():
    # CLI binary missing AND no gui block -> 0 accounts (clean disable).
    cfg = {
        "prefer": "cli",
        "models": ["workbuddy/default"],
        "cli": {
            "binary": "definitely-not-a-real-binary-xyz",
            "models": ["workbuddy/default"],
            "accounts": [{"id": "c"}],
        },
    }
    prov = WorkbuddyHybridProvider(cfg, None)
    accs = asyncio.run(prov.discover_accounts())
    assert accs == [], "no CLI (bad binary) + no gui => 0 accounts"
    print("  ok: WorkbuddyHybrid disables cleanly with no CLI and no GUI")


# ── a broken CLI run is a failure, never an answer ───────────────────────────
# Measured on the real CodeBuddy Code CLI: signed out it exits **0** and puts the
# hint on stderr; the WorkBuddy-embedded build puts the same hint on stdout. A
# returncode check alone lets both through, and the gateway would serve an empty
# (or hint-text) completion that upstream code cannot tell from a real answer.

_SENTINEL_MODELS = ["workbuddy_cli/m"]
_SENTINEL_MAP = {"workbuddy_cli/m": "m"}


def _fake_prov(**extra):
    prov = WorkbuddyCliProvider(_cli_cfg(_SENTINEL_MODELS, _SENTINEL_MAP, **extra), None)
    asyncio.run(prov.discover_accounts())
    return prov


def _req(prompt: str) -> dict:
    return {"model": "workbuddy_cli/m", "messages": [{"role": "user", "content": prompt}]}


def test_signed_out_cli_is_a_failure_not_an_answer():
    for sentinel in ("SIMULATE_SIGNED_OUT_STDERR", "SIMULATE_SIGNED_OUT_STDOUT"):
        prov = _fake_prov()
        try:
            asyncio.run(prov.chat(prov.accounts[0], _req(f"{sentinel} hi")))
            raise AssertionError(f"{sentinel}: a signed-out CLI must not answer")
        except UpstreamError as exc:
            assert exc.reauth is True, f"{sentinel}: expected reauth, got {exc}"
            assert exc.status == 401, f"{sentinel}: {exc.status}"
    print("  ok: signed-out CLI -> reauth on both stdout and stderr shapes")


def test_silent_cli_is_a_failure():
    prov = _fake_prov()
    try:
        asyncio.run(prov.chat(prov.accounts[0], _req("SIMULATE_EMPTY hi")))
        raise AssertionError("an exit-0-says-nothing CLI must raise")
    except UpstreamError as exc:
        assert exc.status == 502, exc.status
        assert exc.reauth is False, "no auth hint -> not a reauth"
    print("  ok: empty stdout -> failure, not an empty completion")


def test_stream_raises_when_cli_is_signed_out():
    prov = _fake_prov()

    async def _drive():
        async for _ in prov.chat_stream(prov.accounts[0], _req("SIMULATE_SIGNED_OUT_STDERR hi")):
            pass

    try:
        asyncio.run(_drive())
        raise AssertionError("signed-out stream must raise instead of finishing clean")
    except UpstreamError as exc:
        assert exc.reauth is True, exc
    print("  ok: chat_stream() refuses to finish clean when the CLI is signed out")


def test_stream_raises_on_nonzero_exit():
    prov = _fake_prov()

    async def _drive():
        async for _ in prov.chat_stream(prov.accounts[0], _req("SIMULATE_FAIL_EXIT hi")):
            pass

    try:
        asyncio.run(_drive())
        raise AssertionError("non-zero CLI exit must surface in stream mode too")
    except UpstreamError as exc:
        assert exc.status == 502, exc.status
    print("  ok: chat_stream() propagates a non-zero exit")


def test_stream_drains_stderr_and_still_answers():
    """A chatty stderr used to be unread -> the child blocks on a full pipe buffer."""
    prov = _fake_prov()
    chunks: list[dict] = []

    async def _drive():
        async def _inner():
            async for c in prov.chat_stream(prov.accounts[0], _req("SIMULATE_STDERR_NOISE hi")):
                chunks.append(c)

        await asyncio.wait_for(_inner(), timeout=60)

    asyncio.run(_drive())
    content = "".join(
        c["choices"][0]["delta"].get("content", "")
        for c in chunks
        if "content" in c["choices"][0]["delta"]
    )
    assert "SIMULATE_STDERR_NOISE" in content, content
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"
    print("  ok: large stderr is drained, the answer still streams")


def test_refresh_models_reads_the_clis_own_list():
    """裁定 46 的同一条判据往下层用一次：模型名单由被调方声明，不抄进配置。"""
    prov = _fake_prov()
    short = asyncio.run(prov.refresh_models(prov.accounts[0]))
    assert short == ["alpha-1", "beta-2", "gamma-3"], short
    assert prov.served_models == (
        "workbuddy_cli/alpha-1",
        "workbuddy_cli/beta-2",
        "workbuddy_cli/gamma-3",
    ), prov.served_models
    assert prov.model_map["workbuddy_cli/beta-2"] == "beta-2"
    assert prov.models_refreshed is True
    print("  ok: refresh_models() takes the CLI's advertised list and replaces the seed")


def test_hybrid_keeps_the_public_id_prefix_when_discovering():
    """The inner CLI's registry key must not leak into ids clients see."""
    cfg = {
        "prefer": "cli",
        "models": ["workbuddy/seed"],
        "cli": {
            "binary": FAKE,
            "interpreter": PY,
            "prompt_flag": "-p",
            "model_flag": "--model",
            "models": ["workbuddy/seed"],
            "accounts": [{"id": "c"}],
        },
    }
    prov = WorkbuddyHybridProvider(cfg, None)
    asyncio.run(prov.discover_accounts())
    short = asyncio.run(prov.refresh_models(prov.accounts[0]))
    assert short == ["alpha-1", "beta-2", "gamma-3"], short
    assert prov.served_models == (
        "workbuddy/alpha-1",
        "workbuddy/beta-2",
        "workbuddy/gamma-3",
    ), prov.served_models
    assert prov._cli.served_models[0] == "workbuddy/alpha-1", prov._cli.served_models
    print("  ok: hybrid lifts the discovered catalog under its own public prefix")


def test_refresh_models_keeps_the_seed_when_nothing_is_advertised():
    prov = _fake_prov(env={"FAKE_CLI_HELP_NO_LIST": "1"})
    assert asyncio.run(prov.refresh_models(prov.accounts[0])) is None
    assert prov.served_models == tuple(_SENTINEL_MODELS), "seed must survive"
    assert prov.models_refreshed is False
    print("  ok: no advertised list -> seed catalog kept")


def main_run():
    print("=== cli provider verification ===")
    test_binary_missing_disables()
    test_chat_captures_stdout()
    test_stream_yields_chunks()
    test_e2e_through_gateway()
    test_signed_out_cli_is_a_failure_not_an_answer()
    test_silent_cli_is_a_failure()
    test_stream_raises_when_cli_is_signed_out()
    test_stream_raises_on_nonzero_exit()
    test_stream_drains_stderr_and_still_answers()
    test_refresh_models_reads_the_clis_own_list()
    test_hybrid_keeps_the_public_id_prefix_when_discovering()
    test_refresh_models_keeps_the_seed_when_nothing_is_advertised()
    test_marvis_cli_via_fake()
    test_marvis_gui_graceful_disable()
    test_workbuddy_cli_via_fake()
    test_workbuddy_gui_graceful_disable()
    test_workbuddy_hybrid_prefers_cli()
    test_workbuddy_hybrid_disables_without_cli_or_gui()
    print("\nALL CHECKS PASSED")


if __name__ == "__main__":
    main_run()
