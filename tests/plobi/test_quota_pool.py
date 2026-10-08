"""B3 quota pool tests: source registry, probes, cheap-first routing, failover."""

from __future__ import annotations

import time

import pytest

from plobi.delegation.quota import QuotaCircuit
from plobi.quota.config import DEFAULT_ORDER, DEFAULT_SOURCES, _resolve_source
from plobi.quota.pool import QuotaPool, get_quota_pool, set_quota_pool
from plobi.quota.route import QuotaAwareCompleter, resolve_l2_endpoint
from plobi.quota.sources import (
    AigwSource,
    CheapApiSource,
    HealthStatus,
    SourceStatus,
    build_sources,
    health_rank,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Strip quota env overrides so tests are deterministic."""
    for key in list(pytest.importorskip("os").environ):
        if key.startswith("PLOBI_QUOTA_") or key.startswith("PLOBI_DELEGATION_"):
            monkeypatch.delenv(key, raising=False)
    set_quota_pool(None)


def _src(name, kind, health=HealthStatus.HEALTHY, detail="test"):
    """A source whose probe returns a fixed health (no network)."""
    if kind == "cheap_api":
        src = CheapApiSource(name, model="m", base_url="http://x/v1", api_key="k")
    else:
        src = AigwSource(name, model="m", base_url="http://x/v1", api_key="k")
    src._probe_fn = lambda s: SourceStatus(
        s.name, s.kind, health, None, detail, time.time()
    )
    return src


def _pool(**healths) -> QuotaPool:
    """Build a pool with the given name → (kind, health) mapping."""
    sources = {
        name: _src(name, kind, health)
        for name, (kind, health) in healths.items()
    }
    order = [n for n in DEFAULT_ORDER if n in sources] + [n for n in sources if n not in DEFAULT_ORDER]
    return QuotaPool(sources, order=order)


# ── registry ─────────────────────────────────────────────────────────────────


def test_config_owns_no_desktop_quota_sources():
    """裁定 46: 桌面额度源名单的唯一真源是网关，配置里不许再存一份硬编副本。

    这条是判据而不是快照——将来加第三个 ``cheap_api`` 源不会弄红它，而任何人把
    ``kind: aigw`` 写回 ``DEFAULT_SOURCES``（也就是把第二份清单搬回来）会当场红。
    """
    assert DEFAULT_SOURCES  # 便宜档仍归本配置管
    assert {cfg["kind"] for cfg in DEFAULT_SOURCES.values()} == {"cheap_api"}
    assert set(DEFAULT_SOURCES) == set(DEFAULT_ORDER)


def test_build_sources_dispatches_by_kind():
    cfg = {
        "sources": {
            "zhipu-air": {"kind": "cheap_api", "model": "glm-4-air", "base_url": "u", "api_key": "k"},
            "antigravity": {"kind": "aigw", "model": "antigravity/gemini-3-pro", "base_url": "u", "api_key": "k"},
            "workbuddy": {"kind": "aigw", "model": "workbuddy/deepseek-chat", "base_url": "u", "api_key": "k"},
        }
    }
    sources = build_sources(cfg)
    assert isinstance(sources["zhipu-air"], CheapApiSource)
    assert isinstance(sources["antigravity"], AigwSource)
    assert isinstance(sources["workbuddy"], AigwSource)


def test_resolve_source_env_overrides_file():
    merged = _resolve_source(
        "zhipu-air",
        {"base_url": "file-url"},
        {"PLOBI_QUOTA_ZHIPU_URL": "env-url", "PLOBI_QUOTA_ZHIPU_KEY": "env-key"},
    )
    assert merged["base_url"] == "env-url"  # env wins over file
    assert merged["api_key"] == "env-key"
    assert merged["kind"] == "cheap_api"


# ── WP-BE-2 credential bridge (auth.json credential_pool → quota sources) ───


def test_bridge_fills_key_and_url_when_env_unset(monkeypatch):
    """env 未设 → 只读桥补 api_key / 空 base_url（unavailable → configured）。"""
    from plobi.quota import config as quota_config

    monkeypatch.setattr(
        quota_config, "_lookup_credential", lambda p: ("pool-key", "https://pool.example/v4")
    )
    merged = quota_config._resolve_source("zhipu-air", {}, {})
    assert merged["api_key"] == "pool-key"
    assert merged["base_url"] == "https://pool.example/v4"


def test_bridge_never_overrides_env_or_file(monkeypatch):
    from plobi.quota import config as quota_config

    monkeypatch.setattr(
        quota_config, "_lookup_credential", lambda p: ("pool-key", "pool-url")
    )
    merged = quota_config._resolve_source(
        "zhipu-air",
        {"base_url": "file-url"},
        {"PLOBI_QUOTA_ZHIPU_KEY": "env-key"},
    )
    assert merged["api_key"] == "env-key"  # env beats pool
    assert merged["base_url"] == "file-url"  # file beats pool


def test_bridge_empty_pool_stays_unconfigured(monkeypatch):
    """查不到凭据时诚实留空，不伪造。"""
    from plobi.quota import config as quota_config

    monkeypatch.setattr(quota_config, "_lookup_credential", lambda p: ("", ""))
    merged = quota_config._resolve_source("zhipu-air", {}, {})
    assert merged["api_key"] == ""


def test_lookup_credential_resolves_env_ref(monkeypatch):
    """``env:VAR`` 引用条目：token 走**同一个凭据入口**解析，url 取条目。

    入口是 ``plobi_cli.config.get_env_value_prefer_dotenv``（注入 > 根家目录 ``.env``
    > 作用域 > shell）。以前这里直接桩 ``load_env()`` —— 那条路认不出注入名单，
    分身家的旧复印件就会在便宜源上赢回来（WP-AGENT-SECRET-SOURCE 的洞 B）。
    """
    from plobi.quota import config as quota_config

    fake = [
        {
            "id": "e1",
            "priority": 0,
            "source": "env:GLM_API_KEY",
            "access_token": "",
            "base_url": "https://api.z.ai/api/paas/v4",
        }
    ]
    monkeypatch.setattr("plobi_cli.auth.read_credential_pool", lambda p: fake)
    import plobi_cli.config

    monkeypatch.setattr(
        plobi_cli.config, "load_env", lambda env_path=None: {"GLM_API_KEY": "dot-key"}
    )
    token, url = quota_config._lookup_credential("zai")
    assert token == "dot-key"
    assert url == "https://api.z.ai/api/paas/v4"


def test_lookup_credential_prefers_inline_token_and_highest_priority(monkeypatch):
    from plobi.quota import config as quota_config

    fake = [
        {"id": "low", "priority": 0, "access_token": "inline-low", "base_url": "u0"},
        {"id": "high", "priority": 2, "access_token": "inline-high", "base_url": "u2"},
    ]
    monkeypatch.setattr("plobi_cli.auth.read_credential_pool", lambda p: fake)
    token, url = quota_config._lookup_credential("zai")
    assert (token, url) == ("inline-high", "u2")


def test_lookup_credential_missing_provider_is_empty(monkeypatch):
    from plobi.quota import config as quota_config

    monkeypatch.setattr("plobi_cli.auth.read_credential_pool", lambda p: [])
    assert quota_config._lookup_credential("zai") == ("", "")


def test_bridge_makes_zhipu_probe_configured(monkeypatch, tmp_path):
    """验收链路：桥补齐 key+url 后，probe 不再是 no-credential UNAVAILABLE。"""
    from plobi.quota import config as quota_config
    from plobi.quota.sources import build_sources

    monkeypatch.setattr(
        quota_config, "_lookup_credential", lambda p: ("pool-key", "https://api.z.ai/api/paas/v4")
    )
    cfg = quota_config.load()
    sources = build_sources(cfg)
    zhipu = sources["zhipu-air"]
    assert zhipu.configured
    status = zhipu.probe()
    assert status.health is not HealthStatus.UNAVAILABLE  # 最多降级，不再 no-credential
    assert status.detail != "no credential configured"


# ── probing ─────────────────────────────────────────────────────────────────


def test_probe_all_reports_three_statuses():
    pool = _pool(
        **{
            "zhipu-air": ("cheap_api", HealthStatus.HEALTHY),
            "antigravity": ("aigw", HealthStatus.DEGRADED),
            "workbuddy": ("aigw", HealthStatus.UNAVAILABLE),
        }
    )
    statuses = pool.probe_all()
    assert len(statuses) == 3
    assert [s.name for s in statuses] == ["zhipu-air", "antigravity", "workbuddy"]
    assert statuses[0].health is HealthStatus.HEALTHY
    assert statuses[2].health is HealthStatus.UNAVAILABLE
    assert not statuses[2].usable


def test_health_rank_ordering():
    assert health_rank(HealthStatus.HEALTHY) < health_rank(HealthStatus.DEGRADED)
    assert health_rank(HealthStatus.DEGRADED) < health_rank(HealthStatus.UNAVAILABLE)


# ── routing strategy ────────────────────────────────────────────────────────


def test_resolve_cheap_first():
    pool = _pool(
        **{
            "zhipu-air": ("cheap_api", HealthStatus.HEALTHY),
            "antigravity": ("aigw", HealthStatus.HEALTHY),
            "workbuddy": ("aigw", HealthStatus.HEALTHY),
        }
    )
    assert pool.resolve().name == "zhipu-air"


def test_resolve_skips_unavailable():
    pool = _pool(
        **{
            "zhipu-air": ("cheap_api", HealthStatus.UNAVAILABLE),
            "antigravity": ("aigw", HealthStatus.HEALTHY),
            "workbuddy": ("aigw", HealthStatus.HEALTHY),
        }
    )
    assert pool.resolve().name == "antigravity"


def test_resolve_prefers_healthy_over_degraded():
    # zhipu-air 在前但 degraded；antigravity 在后但 healthy → 选 healthy。
    pool = _pool(
        **{
            "zhipu-air": ("cheap_api", HealthStatus.DEGRADED),
            "antigravity": ("aigw", HealthStatus.HEALTHY),
            "workbuddy": ("aigw", HealthStatus.UNAVAILABLE),
        }
    )
    assert pool.resolve().name == "antigravity"


def test_resolve_falls_back_to_degraded():
    pool = _pool(
        **{
            "zhipu-air": ("cheap_api", HealthStatus.UNAVAILABLE),
            "antigravity": ("aigw", HealthStatus.DEGRADED),
            "workbuddy": ("aigw", HealthStatus.UNAVAILABLE),
        }
    )
    assert pool.resolve().name == "antigravity"


def test_resolve_none_when_all_unavailable():
    pool = _pool(
        **{
            "zhipu-air": ("cheap_api", HealthStatus.UNAVAILABLE),
            "antigravity": ("aigw", HealthStatus.UNAVAILABLE),
            "workbuddy": ("aigw", HealthStatus.UNAVAILABLE),
        }
    )
    assert pool.resolve() is None


def test_mark_failed_triggers_failover():
    pool = _pool(
        **{
            "zhipu-air": ("cheap_api", HealthStatus.HEALTHY),
            "antigravity": ("aigw", HealthStatus.HEALTHY),
            "workbuddy": ("aigw", HealthStatus.UNAVAILABLE),
        }
    )
    assert pool.resolve().name == "zhipu-air"
    pool.mark_failed("zhipu-air")
    assert pool.resolve().name == "antigravity"


# ── B4 circuit wiring ───────────────────────────────────────────────────────


def test_wire_circuit_cheap_api_balance_trips():
    src = CheapApiSource(
        "zhipu-air", model="glm-4-air", base_url="http://x/v1", api_key="k",
        balance_fn=lambda s: 0.0,  # exhausted
    )
    circuit = QuotaCircuit()
    pool = QuotaPool({"zhipu-air": src}, order=["zhipu-air"])
    pool.wire_circuit(circuit)
    pool.probe_all()  # populates the cheap-api balance cache
    verdict = circuit.check()
    assert verdict.allowed is False
    assert "zhipu-air" in verdict.reason


def test_wire_circuit_aigw_unknown_allows():
    src = AigwSource("antigravity", model="m", base_url="http://x/v1", api_key="k")
    circuit = QuotaCircuit()
    pool = QuotaPool({"antigravity": src}, order=["antigravity"])
    pool.wire_circuit(circuit)
    # aigw source reports no dollar balance → breaker sees "unknown" → allow.
    assert src.remaining_usd() is None
    verdict = circuit.check()
    assert verdict.allowed is True


def test_cheap_api_remaining_usd_reflects_balance():
    src = CheapApiSource(
        "zhipu-air", model="glm-4-air", base_url="http://x/v1", api_key="k",
        balance_fn=lambda s: 12.5,
    )
    src.probe()
    assert src.remaining_usd() == 12.5


# ── alerts ──────────────────────────────────────────────────────────────────


def test_alert_statuses_threshold_degraded():
    pool = _pool(
        **{
            "zhipu-air": ("cheap_api", HealthStatus.HEALTHY),
            "antigravity": ("aigw", HealthStatus.DEGRADED),
            "workbuddy": ("aigw", HealthStatus.UNAVAILABLE),
        }
    )
    pool.probe_all()
    alerted = {s.name for s in pool.alert_statuses()}
    assert alerted == {"antigravity", "workbuddy"}  # healthy 不预警


def test_alert_statuses_marks_failed():
    pool = _pool(
        **{
            "zhipu-air": ("cheap_api", HealthStatus.HEALTHY),
            "antigravity": ("aigw", HealthStatus.DEGRADED),
            "workbuddy": ("aigw", HealthStatus.UNAVAILABLE),
        }
    )
    pool.probe_all()
    pool.mark_failed("zhipu-air")
    alerted = {s.name for s in pool.alert_statuses()}
    assert "zhipu-air" in alerted  # 被标记 failed 也进预警


# ── L2 wiring ───────────────────────────────────────────────────────────────


def test_resolve_l2_endpoint_returns_source_endpoint():
    pool = _pool(
        **{
            "zhipu-air": ("cheap_api", HealthStatus.HEALTHY),
            "antigravity": ("aigw", HealthStatus.DEGRADED),
            "workbuddy": ("aigw", HealthStatus.UNAVAILABLE),
        }
    )
    ep = resolve_l2_endpoint(pool)
    assert ep is not None
    assert ep["model"] == "m"
    assert ep["base_url"] == "http://x/v1"


def test_quota_aware_completer_failover():
    pool = _pool(
        **{
            "zhipu-air": ("cheap_api", HealthStatus.HEALTHY),
            "antigravity": ("aigw", HealthStatus.HEALTHY),
            "workbuddy": ("aigw", HealthStatus.UNAVAILABLE),
        }
    )
    calls = []

    def send(prompt, source):
        calls.append(source.name)
        if source.name == "zhipu-air":
            raise RuntimeError("boom")
        return "ok:" + source.name

    completer = QuotaAwareCompleter(pool, send=send, max_attempts=3)
    result = completer("hello", None)
    assert result == "ok:antigravity"
    assert calls == ["zhipu-air", "antigravity"]  # failed over exactly once


def test_quota_aware_completer_none_when_exhausted():
    pool = _pool(
        **{
            "zhipu-air": ("cheap_api", HealthStatus.UNAVAILABLE),
            "antigravity": ("aigw", HealthStatus.UNAVAILABLE),
            "workbuddy": ("aigw", HealthStatus.UNAVAILABLE),
        }
    )
    completer = QuotaAwareCompleter(pool, max_attempts=3)
    assert completer("hello", None) is None
