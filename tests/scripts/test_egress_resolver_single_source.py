"""One table, one reader: the guards that keep this from becoming a second truth source.

The bug class 裁定 94 names is a *narrow reader* — a caller that asks the
network question for itself and thereby misses a fix that was made at the real
entry point.  These are structural assertions about that, not behavior tests.
"""

from __future__ import annotations

import inspect
import subprocess
from pathlib import Path

import pytest
import yaml

from agent import process_bootstrap
from agent.egress import rules as egress
from agent.external_offload import resolve_proxy

REPO = Path(__file__).resolve().parents[2]
PRODUCT_DIRS = ("agent", "tools", "gateway", "plobi_cli", "aigw", "plugins")


def test_only_the_egress_parser_reads_the_config_key():
    hits: list[str] = []
    #: ``plobi_cli/egress_config.py`` names the ``egress`` key only to hand the
    #: block to :func:`agent.egress.rules.parse_table` for validation — it reads
    #: no rule/proxy/mirror semantics itself, so it is not a second interpreter.
    #: Anything else that grows this list is.
    allowed_non_interpreters = {"plobi_cli/egress_config.py"}
    for directory in PRODUCT_DIRS:
        base = REPO / directory
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="replace")
            if 'get("egress")' in text or '["egress"]' in text:
                hits.append(path.relative_to(REPO).as_posix())
    offenders = [h for h in hits if h not in allowed_non_interpreters]
    assert offenders == ["agent/egress/rules.py"], (
        f"a second module now interprets network.egress itself: {offenders}; route it through "
        "agent.egress.rules.load_table/parse_table instead"
    )


def test_spawn_builders_both_project_the_table():
    """A projection that lands in one spawn path and not the other is the same
    bug in a different costume — the rule appears to work and doesn't."""
    from tools.environments.local import _sanitize_subprocess_env, plobi_subprocess_env

    for fn in (plobi_subprocess_env, _sanitize_subprocess_env):
        assert "_apply_egress_routing" in inspect.getsource(fn), fn.__name__


def test_no_new_direct_reader_of_the_environment_in_the_egress_layer():
    """``agent.egress`` reads the table; only the legacy resolver reads the env.

    ``utils.py`` owns the canonical variable-name list and
    ``gateway/platforms/base.py`` is the pre-existing platform reader, so those
    two are named rather than swept up.  Anything else that starts reading proxy
    variables directly has just become a second source of truth.
    """
    allowed = {
        "agent/process_bootstrap.py",
        "agent/egress/env_projection.py",
        "agent/egress/rules.py",
        "utils.py",
        "gateway/platforms/base.py",
        "tools/env_passthrough.py",
    }
    offenders = []
    for path in (REPO / "agent").rglob("*.py"):
        rel = path.relative_to(REPO).as_posix()
        if rel in allowed:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if "_PROXY" in text and "proxy_bypass" in text:
            offenders.append(rel)
    assert not offenders, f"second proxy reader under agent/: {offenders}"


@pytest.fixture()
def home_with_egress(tmp_path, monkeypatch):
    def _write(egress_block: dict) -> Path:
        home = tmp_path / f"home-{abs(hash(yaml.safe_dump(egress_block)))}"
        home.mkdir(parents=True, exist_ok=True)
        (home / "config.yaml").write_text(
            yaml.safe_dump({"network": {"egress": egress_block}}), encoding="utf-8"
        )
        monkeypatch.setenv("PLOBI_HOME", str(home))
        monkeypatch.setattr(egress, "_TABLE_CACHE", None)
        monkeypatch.delenv("PLOBI_OFFLOAD_PROXY", raising=False)
        monkeypatch.setenv("HTTPS_PROXY", "http://inherited.test:9")
        return home

    yield _write
    monkeypatch.setattr(egress, "_TABLE_CACHE", None)


def test_a_caller_with_no_destination_still_gets_the_tables_answer(home_with_egress):
    """``resolve_proxy`` hands its answer to a child that may reach anything.

    It passes ``base_url=None`` to the shared resolver, so a table that only
    answered named URLs would silently not apply to the offload path — the exact
    shape of 裁定 94's bug.  This test does not touch their file; it proves the
    shared entry point answers for them.
    """
    home_with_egress(
        {
            "proxies": {"local": {"url": "http://exit.test:8234"}},
            "rules": [{"match": "*", "via": "proxy:local"}],
        }
    )
    url, source = resolve_proxy()
    assert url == "http://exit.test:8234"
    assert "inherited.test" not in url

    home_with_egress({"rules": [{"match": "*", "via": "direct"}]})
    url, source = resolve_proxy()
    assert url == "" and source == "none"


def test_the_explicit_offload_knob_still_wins_over_the_table(home_with_egress):
    """Their documented precedence order is not mine to change."""
    home_with_egress(
        {
            "proxies": {"local": {"url": "http://exit.test:8234"}},
            "rules": [{"match": "*", "via": "proxy:local"}],
        }
    )
    import os

    os.environ["PLOBI_OFFLOAD_PROXY"] = "http://knob.test:1"
    try:
        url, source = resolve_proxy()
        assert url == "http://knob.test:1" and source == "PLOBI_OFFLOAD_PROXY"
    finally:
        os.environ.pop("PLOBI_OFFLOAD_PROXY", None)


def test_the_keepalive_builder_inherits_the_answer_without_a_new_reader(
    home_with_egress, monkeypatch
):
    """One decision between the table and the client, not two.

    Pinned at the call, because httpx 0.28 exposes no readable proxy attribute —
    what matters for 裁定 94 is that the builder never re-reads the environment
    itself.
    """
    home_with_egress({"rules": [{"match": "*", "via": "direct"}]})
    asked: list = []
    monkeypatch.setattr(
        process_bootstrap,
        "_get_proxy_for_base_url",
        lambda url: asked.append(url) or None,
    )
    client = process_bootstrap.build_keepalive_http_client(base_url="")
    try:
        assert asked == [""]
    finally:
        client.close()


def test_unreadable_is_distinguishable_from_undeclared(monkeypatch):
    """The state a fail-closed caller needs.

    ``load_table`` deliberately does not raise on an unreadable config — the spawn
    path must survive that (it cost 22 consumer-family reds the first time).  But
    "I could not read your routing" and "you declared no routing" are different
    answers, and a caller that refuses to run unrouted (the offload child) cannot
    tell them apart from an inert table alone.  ``Table.unreadable`` carries it.
    """
    import plobi_cli.config as pcfg

    def _boom():
        raise RuntimeError("Could not determine home directory")

    real = pcfg.load_config_readonly
    monkeypatch.setattr(egress, "_TABLE_CACHE", None)
    monkeypatch.setattr(pcfg, "load_config_readonly", _boom)
    unreadable = egress.load_table()

    pcfg.load_config_readonly = real
    egress._TABLE_CACHE = None
    declared_nothing = egress.load_table()

    assert unreadable.unreadable.startswith("RuntimeError")
    assert unreadable.active is False
    assert declared_nothing.unreadable == ""
    assert declared_nothing.active is False


def test_external_offload_source_still_delegates():
    text = (REPO / "agent" / "external_offload.py").read_text(encoding="utf-8")
    assert "_get_proxy_for_base_url" in text, "the offload channel stopped using the shared resolver"
    assert 'get("egress")' not in text


def test_both_consumers_pass_the_one_liveness_gate():
    """A missing engine must produce the same answer for a child and for us.

    ``agent.egress.engine.usable_exit`` is the only place that decides whether an
    exit is there; the spawn projection and the in-process resolver both go
    through it.  A second reader would be 裁定 94 again — and it would be worse
    than the original bug, because one of the two would keep handing children a
    port that refuses every connection.
    """
    from agent.egress import engine, env_projection

    assert "usable_exit" in inspect.getsource(process_bootstrap._get_proxy_for_base_url)
    assert "usable_exit" in inspect.getsource(env_projection.apply_egress_env)

    readers = []
    for directory in PRODUCT_DIRS:
        base = REPO / directory
        for path in base.rglob("*.py") if base.is_dir() else []:
            rel = path.relative_to(REPO).as_posix()
            if rel in {"agent/egress/engine.py", "agent/egress/env_projection.py",
                       "agent/process_bootstrap.py"}:
                continue
            if "exit_reachable" in path.read_text(encoding="utf-8", errors="replace"):
                readers.append(rel)
    assert not readers, f"exit liveness is being decided in a second place: {readers}"
    assert engine.REACH_TTL <= 5.0, "the reading is cached per spawn; do not let it go stale"
