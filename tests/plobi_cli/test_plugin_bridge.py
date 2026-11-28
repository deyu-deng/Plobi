"""**宿主注入的窄接口**（深档机制第二步 · 规格 ``Docs/specs/plugin-sandbox.md`` §2.2）。

形状照 Extism：**权限＝宿主注入哪几个接口**。这一刀的判据是**关系式**的：

- 没被放行的能力**连属性都没有** ⇒ 插件拿不到句柄（不是「调了才报错」）；
- 声明了但**缺边界** ⇒ **不放行**（默认拒绝，不是不设限）；
- 放行范围内的调用**真的能用**（读文件／取网址）；
- **越界一律拒**（路径不在清单里、域名不在清单里、跳转目标不在清单里）；
- ⚠️ **这一份是形状不是牢笼**：进程内的 Python 插件仍能自己 import os（照实说，不许含糊）。
"""

import json
import threading
import types
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from plobi_cli.plugin_capabilities import (
    PluginCapabilities,
    PluginCapabilityError,
    build_plugin_capabilities,
    capability_grantable,
    host_within_limits,
    path_within_limits,
)


def _manifest(**kwargs):
    from plobi_cli.plugins import PluginManifest

    return PluginManifest(name="my-thing", key="my-thing", **kwargs)


# ── 没被放行的能力，连句柄都没有 ─────────────────────────────────────────────


def test_an_ungranted_capability_is_not_even_an_attribute():
    """没放行 ⇒ 插件**拿不到句柄**（Extism 的形状：没注入的 import 压根不存在）。"""
    caps = build_plugin_capabilities(_manifest())

    assert caps.granted == ()
    with pytest.raises(PluginCapabilityError):
        _ = caps.fs
    with pytest.raises(PluginCapabilityError):
        _ = caps.net


def test_a_granted_capability_is_handed_over():
    caps = build_plugin_capabilities(
        _manifest(permissions=["fs.read"], limits={"fs": {"paths": ["/tmp/**"]}})
    )

    assert caps.granted == ("fs.read",)
    assert hasattr(caps, "fs")


def test_declared_but_unbounded_is_not_granted():
    """声明了却**没给边界** ⇒ 不放行（默认拒绝，不是不设限）。"""
    caps = build_plugin_capabilities(_manifest(permissions=["net.fetch"]))

    assert caps.granted == ()
    with pytest.raises(PluginCapabilityError):
        _ = caps.net


def test_shallow_plugins_get_an_empty_bridge():
    """浅档插件（今天全部，没有 permissions:）⇒ 桥是空的，**行为一个字不变**。"""
    caps = build_plugin_capabilities(_manifest())

    assert caps.granted == ()


# ── 边界判定 ─────────────────────────────────────────────────────────────────


def test_path_limits_use_globs(tmp_path):
    """``**`` 跨层、``*`` 单层（路径一律按正斜杠比 ⇒ 一条模式在哪台机器上都一样）。"""
    deep = tmp_path / "a" / "b.txt"
    deep.parent.mkdir(parents=True, exist_ok=True)
    deep.write_text("x", encoding="utf-8")
    shallow = tmp_path / "c.txt"
    shallow.write_text("x", encoding="utf-8")
    root = tmp_path.as_posix()

    assert path_within_limits(str(deep), [f"{root}/**"]) is True
    assert path_within_limits(str(shallow), [f"{root}/**"]) is True
    # ``*`` 不跨层 ⇒ 深一层的那个不中
    assert path_within_limits(str(deep), [f"{root}/*.txt"]) is False
    assert path_within_limits(str(shallow), [f"{root}/*.txt"]) is True
    # 越界的那条不中
    assert path_within_limits(str(deep), [f"{root}/a/../b/**"]) is False
    assert path_within_limits(str(deep), []) is False


def test_host_limits():
    assert host_within_limits("api.example.com", ["api.example.com"]) is True
    assert host_within_limits("API.EXAMPLE.COM", ["api.example.com"]) is True
    assert host_within_limits("evil.com", ["api.example.com"]) is False
    assert host_within_limits("a.example.com", ["*.example.com"]) is True
    assert host_within_limits("example.com", ["*.example.com"]) is False
    assert host_within_limits("api.example.com", []) is False


def test_capability_grantable_reads_the_limits():
    assert capability_grantable("net.fetch", {"net": {"hosts": ["a.com"]}}) is True
    assert capability_grantable("net.fetch", {}) is False
    assert capability_grantable("ui.render", {}) is True


# ── 放行范围内的调用真的能用 ─────────────────────────────────────────────────


def test_reading_a_file_inside_the_limit_works(tmp_path):
    target = tmp_path / "note.md"
    target.write_text("hello", encoding="utf-8")

    caps = build_plugin_capabilities(
        _manifest(permissions=["fs.read"], limits={"fs": {"paths": [str(tmp_path / "**")]}})
    )

    assert caps.fs.read(str(target)) == "hello"


def test_reading_outside_the_limit_is_refused(tmp_path):
    """越界 ⇒ **拒**（权限错误，不是「文件读不到」那种环境错误）。"""
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    allowed = tmp_path / "allowed"
    allowed.mkdir()

    caps = build_plugin_capabilities(
        _manifest(permissions=["fs.read"], limits={"fs": {"paths": [str(allowed / "**")]}})
    )

    with pytest.raises(PluginCapabilityError):
        caps.fs.read(str(outside))


def test_writing_outside_the_limit_is_refused(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()

    caps = build_plugin_capabilities(
        _manifest(permissions=["fs.write"], limits={"fs": {"paths": [str(allowed / "**")]}})
    )

    with pytest.raises(PluginCapabilityError):
        caps.fs.write(str(tmp_path / "outside.txt"), "nope")


def test_writing_inside_the_limit_works(tmp_path):
    target = tmp_path / "sub" / "note.md"

    caps = build_plugin_capabilities(
        _manifest(permissions=["fs.write"], limits={"fs": {"paths": [str(tmp_path / "**")]}})
    )

    caps.fs.write(str(target), "written")

    assert target.read_text(encoding="utf-8") == "written"


# ── 网络 ─────────────────────────────────────────────────────────────────────


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", self.server.redirect_to)  # type: ignore[attr-defined]
            self.end_headers()
            return
        body = json.dumps({"ok": True, "path": self.path}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # keep the test output quiet
        return


@pytest.fixture()
def _http_server():
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    server.redirect_to = "http://127.0.0.1:1/elsewhere"  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


def _caps_for(host: str, limits_hosts):
    return build_plugin_capabilities(
        _manifest(permissions=["net.fetch"], limits={"net": {"hosts": limits_hosts}})
    )


def test_fetching_an_allowed_host_works(_http_server):
    # 域名白名单只看**主机名**（不含端口）—— 口径写在 `host_within_limits` 的注释里。
    host, port = _http_server.server_address
    caps = _caps_for(host, [host])

    response = caps.net.fetch(f"http://{host}:{port}/thing")

    assert response.status == 200
    assert response.json()["path"] == "/thing"


def test_fetching_a_host_outside_the_limit_is_refused(_http_server):
    host, port = _http_server.server_address
    caps = _caps_for(host, ["api.example.com"])

    with pytest.raises(PluginCapabilityError):
        caps.net.fetch(f"http://{host}:{port}/thing")


def test_a_redirect_out_of_the_limit_is_refused(_http_server):
    """一个 302 就能把白名单绕过去 ⇒ **每一跳都重新判**。"""
    host, port = _http_server.server_address
    caps = _caps_for(host, [host])

    with pytest.raises(PluginCapabilityError):
        caps.net.fetch(f"http://{host}:{port}/redirect")


def test_non_http_schemes_are_refused(_http_server):
    host, port = _http_server.server_address
    caps = _caps_for(host, [host])

    with pytest.raises(PluginCapabilityError):
        caps.net.fetch(f"file:///etc/passwd")


# ── 它挂在 ctx 上 ────────────────────────────────────────────────────────────


def test_the_bridge_hangs_off_the_plugin_context():
    """插件拿到的那个东西是 ``ctx.capabilities``（不是环境变量、不是裸 open）。"""
    from plobi_cli.plugins import PluginContext

    manager = types.SimpleNamespace(_plugin_tool_names=set())
    manifest = _manifest(
        permissions=["fs.read"], limits={"fs": {"paths": ["/tmp/**"]}}
    )

    ctx = PluginContext(manifest, manager)  # type: ignore[arg-type]

    assert isinstance(ctx.capabilities, PluginCapabilities)
    assert ctx.capabilities.granted == ("fs.read",)
