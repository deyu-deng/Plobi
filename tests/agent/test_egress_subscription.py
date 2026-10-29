"""Subscription import: the table must end up holding names, and secrets must not (WP-EGRESS-ROUTING).

Two things this file pins, both from the shape the user ruled (10-16, 甲：点一下才拉、解析进表):

* a link list, a base64-wrapped link list, and a Clash yaml all land as the *same* table entries,
  with every credential replaced by a `*_env` name and the value handed to the root `.env` writer —
  so nothing that gets logged, PUT, or screenshotted can contain a password;
* a format we don't understand is refused **in a sentence the user can act on**, and an unreachable
  subscription fails once (no silent retry — this box's precedent burned 114,124 input tokens and
  produced an empty artifact directory).
"""

from __future__ import annotations

import base64
import json

import pytest

from agent.egress import subscription as sub

B64_SS = "ss://" + "YWExMjM6cGFzc3dvcmQx".replace("\n", "") + "@203.0.113.9:8388#%E4%B8%9C%E4%BA%AC"
TROJAN = "trojan://s3cr3t-trojan@198.51.100.7:443?allowInsecure=1&sni=eu.example#德国"
VMESS = "vmess://" + base64.urlsafe_b64encode(json.dumps(
    {"ps": "美国", "add": "198.51.100.2", "port": 443, "id": "uuid-1111",
     "aid": 0, "net": "ws", "tls": "tls", "host": "a.example", "path": "/x"}).encode()).decode()
CLASH_YAML = """
proxies:
  - name: 东京
    type: ss
    server: 203.0.113.9
    port: 8388
    cipher: aes-256-gcm
    password: password1
  - name: 德国
    type: trojan
    server: 198.51.100.7
    port: 443
    password: s3cr3t-trojan
proxy-groups:
  - name: 自动选择
    type: url-test
    proxies: [东京, 德国, DIRECT]
    url: http://www.gstatic.com/generate_204
    interval: 300
"""


@pytest.fixture
def env_writer(monkeypatch):
    """Capture what would go to `.env` instead of touching this machine's real secrets file."""
    written: dict[str, str] = {}

    import plobi_cli.config as cfg

    monkeypatch.setattr(cfg, "save_env_value_secure", lambda k, v: written.__setitem__(k, v))
    return written


def _assert_no_secret_shaped_thing_leaked(result, secrets):
    blob = json.dumps(result, ensure_ascii=False, default=str)
    for secret in secrets:
        assert secret not in blob, f"{secret!r} 出现在返回值里——表/日志/截图就会带上它"


def test_links_base64_and_clash_yaml_all_land_as_table_entries_with_env_names(env_writer):
    from_links = sub.update(text="\n".join([B64_SS, TROJAN, VMESS]), write_secrets=True)
    from_yaml = sub.update(text=CLASH_YAML, write_secrets=True)

    assert from_links["ok"] and from_yaml["ok"], (from_links["notes"], from_yaml["notes"])
    assert from_links["format"] == "link-list" and from_yaml["format"] == "clash-yaml"

    by_name = {n["name"]: n for n in from_yaml["nodes"]}
    assert set(by_name) == {"东京", "德国"}, from_yaml["nodes"]
    assert by_name["东京"]["type"] == "ss" and by_name["东京"]["cipher"] == "aes-256-gcm"
    assert by_name["德国"]["type"] == "trojan" and by_name["德国"]["port"] == 443

    # the credential is a *name* in the table and a value only in .env — that is the whole shape
    for node in from_yaml["nodes"]:
        env_name = node.get("password_env")
        assert env_name and env_name.startswith("PLOBI_EGRESS_"), node
        assert "password" not in node, node
    assert set(from_yaml["secret_env_names"]) <= set(env_writer), (env_writer, from_yaml["secret_env_names"])
    # 每个成员的名字 = `<节点名>_<端口>_<字段>`：重导一次落到同一个变量上（更新，不是又开一个）
    for node in from_yaml["nodes"]:
        env = node["password_env"]
        assert env.startswith("PLOBI_EGRESS_") and env.endswith(f"_{node['port']}_PASSWORD"), node
        assert env_writer[env] in ("password1", "s3cr3t-trojan"), (env, env_writer[env])

    # a link list is the same content: same names, same two env vars, same nothing-secret
    link_names = {n["name"] for n in from_links["nodes"]}
    assert {"东京", "德国"} <= link_names, link_names
    vmess = next(n for n in from_links["nodes"] if n["type"] == "vmess")
    assert vmess["uuid_env"] and vmess["server"] == "198.51.100.2"

    # a group that referenced the engine's own built-in policy keeps the real members and says so
    group = from_yaml["groups"][0]
    assert group["type"] == "url-test" and group["include"] == ["东京", "德国"], group
    assert any("DIRECT" in note for note in from_yaml["notes"]), from_yaml["notes"]

    _assert_no_secret_shaped_thing_leaked(from_yaml, ["password1", "s3cr3t-trojan"])
    _assert_no_secret_shaped_thing_leaked(from_links, ["password1", "s3cr3t-trojan", "uuid-1111"])


def test_unrecognised_shapes_and_a_dead_subscription_refuse_in_one_actionable_sentence(env_writer, monkeypatch):
    with pytest.raises(sub.SubscriptionError) as exc:
        sub.parse(json.dumps({"outbounds": [{"type": "shadowsocks", "tag": "x"}]}))
    assert "sing-box" in str(exc.value), str(exc.value)

    with pytest.raises(sub.SubscriptionError) as exc:
        sub.parse("hysteria2://key@example:443#日本")
    assert "不认识" in str(exc.value), str(exc.value)

    with pytest.raises(sub.SubscriptionError) as exc:
        sub.parse("这不是订阅，是一段中文")
    assert "认不出来" in str(exc.value), str(exc.value)

    # unreachable: exactly one attempt, and the sentence names the exit it tried
    calls = []

    def boom(request, *a, **kw):
        calls.append(request)
        raise OSError("dial tcp 198.18.0.45:443: connectex failed")

    monkeypatch.setattr(sub.urllib.request, "build_opener", lambda *h: type("O", (), {"open": boom})())
    with pytest.raises(sub.SubscriptionError) as exc:
        sub.fetch("https://subscribe.example/a?token=***")
    assert "网络不可达" in str(exc.value), str(exc.value)
    assert len(calls) == 1, "静默重试是这台的前例：一次都不许多"

    with pytest.raises(sub.SubscriptionError) as exc:
        sub.fetch("D:/Temp/not-a-url.yaml")
    assert "导入池子" in str(exc.value), "路径那种要走另一颗按钮，别说含糊的话"


def test_a_direct_pull_never_falls_back_to_the_system_proxy(monkeypatch):
    """10-29 现核：表说「直连」，urllib 却用了注册表里那个死掉的系统代理 ⇒ 10061，而报的是「直连」。

    没有 ProxyHandler 时 urllib 会去读 `getproxies()`（Windows 上就是注册表），所以「直连」必须
    显式装一个空映射，而不是什么都不装。
    """
    from agent.egress import subscription

    seen: dict = {}

    class FakeOpener:
        def open(self, request, timeout=0):
            raise OSError("stop before the network")

    def fake_build(*handlers):
        seen["handlers"] = handlers
        return FakeOpener()

    monkeypatch.setattr("agent.process_bootstrap._get_proxy_for_base_url", lambda url: "")
    monkeypatch.setattr(subscription.urllib.request, "build_opener", fake_build)

    with pytest.raises(subscription.SubscriptionError):
        subscription.fetch("https://example.invalid/sub.yaml")

    proxy_handlers = [h for h in seen["handlers"] if isinstance(h, subscription.urllib.request.ProxyHandler)]
    assert proxy_handlers, seen["handlers"]
    assert proxy_handlers[0].proxies == {}, "直连＝空映射；空字符串会被当成没有主机"
