"""The probe that runs before OAuth/quota: any HTTP response is reachable."""

from __future__ import annotations

from quota_hub.providers.apps.antigravity import network as network_probe


class _Response:
    status_code = 404


class _Client:
    def __init__(self, **_kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def get(self, _url):
        return _Response()


class _BoomClient(_Client):
    def get(self, _url):
        raise OSError("getaddrinfo failed")


def test_any_http_response_counts_as_reachable(monkeypatch):
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("HTTP_PROXY", raising=False)

    result = network_probe.probe_network(
        (("google-oauth", "https://oauth2.googleapis.com/"),), client_factory=_Client
    )

    assert result["ok"] is True
    assert result["checks"][0]["status"] == 404
    assert result["failed"] == []


def test_failure_names_the_hosts_and_masks_proxy_credentials(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://user:pass@127.0.0.1:7890")

    result = network_probe.probe_network(
        (
            ("google-accounts", "https://accounts.google.com/"),
            ("cloud-code", "https://daily-cloudcode-pa.googleapis.com/"),
        ),
        client_factory=_BoomClient,
    )

    assert result["ok"] is False
    assert result["failed"] == ["google-accounts", "cloud-code"]
    assert result["proxy"] == "http://***:***@127.0.0.1:7890"
    # 2026-10-28 文案改向：节点明明在跑、只是这条路被节点不放行时，"确认代理还开着"是错的
    # 指导；能做的事是换一个节点，而那个入口在屏上叫「设置 → VPN」。
    assert "节点" in result["advice"]
    assert "VPN" in result["advice"]
    assert "pass" not in result["proxy"]
