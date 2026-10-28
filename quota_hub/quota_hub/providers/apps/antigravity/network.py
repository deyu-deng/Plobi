"""Network reachability probe for Google / Antigravity quota endpoints.

The quota path and the OAuth login both leave the machine. In a mainland-China
network those hosts are unreachable unless a working local proxy is configured,
and a dead proxy must be named as such — not reported later as "OAuth failed"
or "no quota writer". This probe is the one place that says which host failed
and what to check.

It never sends credentials and never spends quota: a plain HTTPS GET is enough;
**any HTTP response counts as reachable** (401/403/404 all prove DNS + TCP +
TLS worked). Only transport failures (DNS, connect, TLS, timeout) fail a check.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit

import httpx

from ....egress import proxy_url as egress_proxy_url

#: Hosts used by the OAuth flow / Cloud Code quota API.
DEFAULT_ENDPOINTS: tuple[tuple[str, str], ...] = (
    ("google-accounts", "https://accounts.google.com/"),
    ("google-oauth", "https://oauth2.googleapis.com/"),
    ("cloud-code", "https://daily-cloudcode-pa.googleapis.com/"),
)


def _configured_proxy() -> tuple[str, str]:
    """The route a Google leg will actually take: the Settings → VPN node first, then env.

    Measured 2026-10-28: the probe read only `HTTP(S)_PROXY`, so it could report "no proxy"
    about to be wrong, while the login was already leaving through the node — and the advice
    line then described a network that was not the one in use. One door for every Google
    leg (裁定 206), so one door for the diagnosis too.
    """
    node, reason = egress_proxy_url()
    if node:
        return node, "ok"
    env = (
        os.environ.get("HTTPS_PROXY")
        or os.environ.get("https_proxy")
        or os.environ.get("HTTP_PROXY")
        or os.environ.get("http_proxy")
        or os.environ.get("ALL_PROXY")
        or os.environ.get("all_proxy")
        or ""
    ).strip()
    if env:
        return env, "ok"
    return "", reason


def _mask(raw: str) -> str:
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
        if parts.username or parts.password:
            host = parts.hostname or ""
            port = f":{parts.port}" if parts.port else ""
            return urlunsplit((parts.scheme, f"***:***@{host}{port}", parts.path, "", ""))
    except ValueError:
        return "configured (unparseable)"
    return raw


def _kind(error: Exception) -> str:
    name = type(error).__name__.lower()
    text = str(error).lower()
    if "timeout" in name or "timed out" in text:
        return "timeout"
    if "ssl" in name or "tls" in text or "certificate" in text:
        return "tls"
    if "name" in name or "dns" in text or "getaddrinfo" in text:
        return "dns"
    return "connect"


def probe_network(
    endpoints: tuple[tuple[str, str], ...] = DEFAULT_ENDPOINTS,
    *,
    timeout: float = 5.0,
    total_timeout: float = 8.0,
    client_factory: Callable[..., Any] | None = None,
) -> dict:
    """Return a JSON-able probe result. Never raises, never blocks past ``total_timeout``.

    Each endpoint runs in a daemon thread because Windows DNS can sit in a
    blocking call longer than httpx's own timeout; the caller must get an answer
    (and a useful advice line) instead of a frozen Connect button.
    """
    factory = client_factory or (lambda **kw: httpx.Client(**kw))
    raw_proxy, node_reason = _configured_proxy()
    # The probe measures the route the leg will really take — including the proxy itself.
    # With `trust_env=True` it measured the ambient environment instead, so a machine whose
    # hub runs direct-but-proxied-per-leg produced advice about a network nobody was using.
    proxy = _mask(raw_proxy)
    results: dict[int, dict] = {}

    def _one(index: int, name: str, url: str) -> None:
        started = time.time()
        try:
            with factory(proxy=raw_proxy or None, trust_env=False, timeout=timeout, follow_redirects=False) as client:
                response = client.get(url)
            results[index] = {
                "name": name,
                "url": url,
                "ok": True,
                "status": int(response.status_code),
                "latency_ms": round((time.time() - started) * 1000),
                "error": "",
            }
        except Exception as exc:
            results[index] = {
                "name": name,
                "url": url,
                "ok": False,
                "status": 0,
                "latency_ms": round((time.time() - started) * 1000),
                "error": f"{_kind(exc)}: {exc}"[:300],
            }

    threads = [
        threading.Thread(target=_one, args=(index, name, url), daemon=True)
        for index, (name, url) in enumerate(endpoints)
    ]
    for thread in threads:
        thread.start()
    deadline = time.time() + total_timeout
    for thread in threads:
        thread.join(max(0.0, deadline - time.time()))

    checks: list[dict] = []
    for index, (name, url) in enumerate(endpoints):
        checks.append(results.get(index) or {
            "name": name,
            "url": url,
            "ok": False,
            "status": 0,
            "latency_ms": round(total_timeout * 1000),
            "error": "timeout: probe did not finish in time",
        })

    failed = [check for check in checks if not check["ok"]]
    if not proxy:
        advice = (
            "这台机器直连 Google 不通，而设置里的 VPN 还没有可用节点。"
            "在「设置 → VPN」加一个节点再点一次就行，不用开 TUN。"
            if node_reason == "no_proxy"
            else "设置里的 VPN 节点现在连不上。确认那个代理还开着、端口没改。"
        )
    else:
        advice = (
            f"出口 {proxy} 已配置，但 Google 端点连不上。确认那个代理还开着、端口正确，"
            "并且浏览器也能打开 accounts.google.com。不用开 TUN。"
        )
    return {
        "ok": not failed,
        "checked_at": int(time.time()),
        "proxy": proxy,
        "checks": checks,
        "failed": [check["name"] for check in failed],
        "advice": "" if not failed else advice,
    }
