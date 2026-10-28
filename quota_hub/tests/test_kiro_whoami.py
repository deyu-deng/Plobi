"""Kiro 这条腿的登录判词必须问 CLI 自己，不能只问「我们有没有点过登录」。

实测 2026-10-28：这台机器上 `kiro-cli whoami` 回 `Logged in with GitHub`——用户早就登录了，
而 hub 因为 `login_ledger` 只记「我们亲手发起并退出 0 的那次登录」，把这条腿判成 `not_signed_in`，
设置页就一直给他一个 `+`。这不是凭据坏了，是我们的**证据面少了**。

本文件不碰真 CLI、不花额度：`subprocess.run` 换成替身，只看我们解析与缓存的形状，
并且钉住「它打印出来的邮箱不许进任何结论字符串」。

Run:
    cd Code && scripts/run_tests.sh -j 1 quota_hub/tests/test_kiro_whoami.py
"""

from __future__ import annotations

from quota_hub import login_ledger
from quota_hub.providers.apps.kiro.cli import KiroCliProvider


class _Result:
    def __init__(self, code: int, out: str = "", err: str = "") -> None:
        self.returncode = code
        self.stdout = out
        self.stderr = err


def _provider(monkeypatch) -> KiroCliProvider:
    prov = KiroCliProvider({"binary": "kiro-cli"}, None)
    monkeypatch.setattr(KiroCliProvider, "_binary_present", lambda self: True)
    return prov


def _fake_run(monkeypatch, provider: KiroCliProvider, result: _Result):
    calls: list[list[str]] = []

    def _run(argv, **kwargs):
        calls.append(list(argv))
        return result

    monkeypatch.setattr("quota_hub.providers.apps.kiro.cli.subprocess.run", _run)
    monkeypatch.setattr("quota_hub.providers.apps.kiro.cli.shutil.which", lambda name: r"C:\fake\kiro-cli.exe")
    return calls


def test_the_cli_saying_logged_in_is_enough_evidence_for_the_card(monkeypatch):
    prov = _provider(monkeypatch)
    calls = _fake_run(monkeypatch, prov, _Result(0, "Logged in with GitHub\nEmail: someone@example.test\n"))

    assert prov.credential_health() == (True, "ok")
    assert calls == [[r"C:\fake\kiro-cli.exe", "whoami"]]
    # 它回的是邮箱，不是给我们上屏的句子。
    assert "example.test" not in str(prov.credential_health())


def test_a_cli_that_says_otherwise_still_reports_not_signed_in(monkeypatch):
    prov = _provider(monkeypatch)
    _fake_run(monkeypatch, prov, _Result(1, "Not logged in"))

    assert prov.credential_health() == (False, "not_signed_in")


def test_the_whoami_answer_is_reused_instead_of_spawning_every_poll(monkeypatch):
    """健康检查是常态脉冲，一条腿一次判词要问外部进程——所以它必须带 TTL。"""
    prov = _provider(monkeypatch)
    calls = _fake_run(monkeypatch, prov, _Result(0, "Logged in with GitHub"))

    for _ in range(4):
        assert prov.credential_health()[0] is True

    assert len(calls) == 1
    prov._whoami_seen = (0.0, False)  # TTL 到期
    assert prov.credential_health() == (True, "ok")
    assert len(calls) == 2


def test_a_login_we_started_ourselves_short_circuits_the_probe(monkeypatch):
    """`login_ledger` 仍是更强的证据：我们亲眼看完一次登录，就不必再去问。"""
    prov = _provider(monkeypatch)
    calls = _fake_run(monkeypatch, prov, _Result(0, "Logged in with GitHub"))
    login_ledger.remember(prov.name)

    assert prov.credential_health() == (True, "ok")
    assert calls == []


def test_an_unreachable_cli_is_not_reported_as_signed_in(monkeypatch):
    prov = _provider(monkeypatch)
    monkeypatch.setattr("quota_hub.providers.apps.kiro.cli.shutil.which", lambda name: None)

    assert prov.credential_health() == (False, "not_signed_in")


def test_a_whoami_that_hangs_does_not_take_the_hub_down(monkeypatch):
    prov = _provider(monkeypatch)
    real_timeout = __import__("subprocess").TimeoutExpired

    def _boom(argv, **kwargs):
        raise real_timeout(cmd=argv, timeout=8)

    monkeypatch.setattr("quota_hub.providers.apps.kiro.cli.subprocess.run", _boom)
    monkeypatch.setattr("quota_hub.providers.apps.kiro.cli.shutil.which", lambda name: r"C:\fake\kiro-cli.exe")

    assert prov.credential_health() == (False, "not_signed_in")
