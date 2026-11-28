"""插件的**窄接口**（深档机制第二步 · 规格 ``Docs/specs/plugin-sandbox.md`` §2.2）。

形状照 Extism：**权限＝宿主注入哪几个接口**；插件的 import 集合就是它的权限集合。
这里给的是它的 Python 那一面——插件拿到的**不是环境**（不是 ``os.environ``、不是裸
``open``），是几个**窄对象**：

.. code-block:: python

    def apply(ctx):
        text = ctx.capabilities.fs.read("/data/notes.md")
        resp = ctx.capabilities.net.fetch("https://api.example.com/v1/thing")

**没被放行的能力连属性都没有** ⇒ 插件拿不到句柄（不是「调了才报错」）。

⚠️ **这一份是形状，不是牢笼**（照实说，不许含糊）：它把「插件能碰什么」变成**宿主给的
窄对象**，每一项调用都按清单与边界判；**但进程内的 Python 插件仍能自己 import os 去碰
任何东西** ⇒ 「只能碰它声明的」这句话要等**进程隔离**那一步（深档的后续一刀）才真正成立。
这一刀先把这套**形状**与**边界判定**立起来，让窄接口成为插件的**正式路径**。
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlsplit

__all__ = [
    "PLUGIN_CAPABILITIES",
    "PluginCapabilities",
    "PluginCapabilityError",
    "PluginFileCapabilities",
    "PluginHttpResponse",
    "PluginNetCapabilities",
    "build_plugin_capabilities",
    "capability_grantable",
]


# ── 能力名的**闭集**（规格 §2.1）─────────────────────────────────────────────
#
# 宿主认得下面这几个；**别的名字一律不认** ⇒ 那个插件**装不上并把原因说清**
# （不是静默忽略——裁定 239 补记 1「失败可见」）。
PLUGIN_CAPABILITIES: frozenset[str] = frozenset(
    {
        "fs.read",
        "fs.write",
        "net.fetch",
        "config.read",
        "config.write",
        "model.invoke",
        "tools.register",
        "ui.render",
    }
)

# 哪几项能力**必须有边界**才可放行（**缺边界 ⇒ 默认拒绝**，不是「不设限」）。
# 抄 Extism 的 `allowed_hosts`／`allowed_paths`：那两项缺省＝**不给**。
_CAPABILITY_REQUIRES_LIMIT: Dict[str, Tuple[str, str]] = {
    "net.fetch": ("net", "hosts"),
    "fs.read": ("fs", "paths"),
    "fs.write": ("fs", "paths"),
}


def capability_grantable(capability: str, limits: Mapping[str, Any]) -> bool:
    """这项能力**能不能放行**（缺边界 ⇒ 默认拒绝；规格 §2.1）。"""
    need = _CAPABILITY_REQUIRES_LIMIT.get(capability)
    if need is None:
        return True

    group, key = need
    values = limits.get(group)
    if not isinstance(values, Mapping):
        return False

    listed = values.get(key)

    return isinstance(listed, list) and bool(listed)


class PluginCapabilityError(RuntimeError):
    """插件要一项它**没有被放行**的能力（规格 §2.2）。

    这不是「文件读不到」那类环境错误，而是**权限**错误：宿主不给你这件事。
    """


@dataclass
class PluginHttpResponse:
    """``ctx.capabilities.net.fetch()`` 的回包（只给数据，不给连接本身）。"""

    status: int
    body: bytes
    headers: Dict[str, str] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self) -> Any:
        return json.loads(self.text)


# ── 边界判定的两件小工具 ─────────────────────────────────────────────────────


def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    """把一条路径模式翻成正则：``**`` 跨层、``*`` 单层、``?`` 单字符。

    路径一律按**正斜杠**比（Windows 上也是）⇒ 一条模式在哪台机器上都一样。
    """
    normalized = pattern.strip().replace("\\", "/")
    out: List[str] = []
    index = 0

    while index < len(normalized):
        if normalized.startswith("**", index):
            out.append(".*")
            index += 2
        elif normalized[index] == "*":
            out.append("[^/]*")
            index += 1
        elif normalized[index] == "?":
            out.append("[^/]")
            index += 1
        else:
            out.append(re.escape(normalized[index]))
            index += 1

    return re.compile("^" + "".join(out) + "$", re.IGNORECASE)


def _resolve_path(path: str) -> Path:
    """展开 ``~`` 并**解析**（解析会跟随符号链接 ⇒ 越界的软链在解析后露馅）。"""
    expanded = os.path.expanduser(path.strip())
    try:
        return Path(expanded).resolve()
    except OSError:
        return Path(expanded).absolute()


def path_within_limits(path: str, patterns: Sequence[str]) -> bool:
    """这个路径**在不在**放行的范围里（``fs.paths`` 那几条模式）。"""
    if not patterns:
        return False

    target = _resolve_path(path).as_posix()

    for pattern in patterns:
        expanded = os.path.expanduser(pattern.strip()).replace("\\", "/")
        try:
            regex = _glob_to_regex(expanded)
        except re.error:
            continue
        if regex.match(target):
            return True

    return False


def host_within_limits(hostname: str, patterns: Sequence[str]) -> bool:
    """这个域名**在不在**放行的范围里（``net.hosts`` 那几条）。

    只看**主机名**（不含端口）⇒ ``api.example.com`` 那一条也放行 ``api.example.com:8080``
    （同一台机器上的另一个端口）；``*.example.com`` 放行它的子域（但不含它自己）。
    """
    host = (hostname or "").strip().lower()
    if not host:
        return False

    for pattern in patterns:
        candidate = pattern.strip().lower()
        if not candidate:
            continue
        if candidate.startswith("*."):
            suffix = candidate[1:]  # ".example.com"
            if host.endswith(suffix) and host != suffix[1:]:
                return True
        elif candidate == host:
            return True

    return False


def _limit_paths(limits: Mapping[str, Any]) -> List[str]:
    fs = limits.get("fs")
    paths = fs.get("paths") if isinstance(fs, Mapping) else None

    return [str(item) for item in paths] if isinstance(paths, list) else []


def _limit_hosts(limits: Mapping[str, Any]) -> List[str]:
    net = limits.get("net")
    hosts = net.get("hosts") if isinstance(net, Mapping) else None

    return [str(item) for item in hosts] if isinstance(hosts, list) else []


# ── 两组窄能力 ───────────────────────────────────────────────────────────────


class PluginFileCapabilities:
    """``ctx.capabilities.fs`` —— 文件那一族（``fs.read``／``fs.write``）。

    **路径必须在放行的范围里**（``fs.paths``）；越界 ⇒ 拒（不是文件错误，是权限错误）。
    """

    def __init__(self, granted: Sequence[str], patterns: Sequence[str]) -> None:
        self._granted = frozenset(granted)
        self._patterns = list(patterns)

    def _require(self, capability: str) -> None:
        if capability not in self._granted:
            raise PluginCapabilityError(
                f"能力 {capability!r} 未放行（这个插件被放行的：{', '.join(sorted(self._granted)) or '（没有）'}）"
            )

    def _target(self, path: str, capability: str) -> Path:
        self._require(capability)
        if not path_within_limits(path, self._patterns):
            raise PluginCapabilityError(
                f"路径 {path!r} 不在放行范围里（放行的：{', '.join(self._patterns) or '（没有）'}）"
            )
        return _resolve_path(path)

    def read(self, path: str) -> str:
        """读一个放行范围内的文件（文本）。"""
        target = self._target(path, "fs.read")
        try:
            return target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise PluginCapabilityError(f"读不了 {path!r}：{exc}") from exc

    def write(self, path: str, content: str) -> None:
        """写一个放行范围内的文件（父目录没有就建）。"""
        target = self._target(path, "fs.write")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        except OSError as exc:
            raise PluginCapabilityError(f"写不了 {path!r}：{exc}") from exc


class _HostCheckedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """每一跳都重新判域名——不然一个 302 就能把白名单绕过去。"""

    def __init__(self, hosts: Sequence[str]) -> None:
        self._hosts = list(hosts)

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        parsed = urlsplit(newurl)
        if parsed.scheme not in ("http", "https"):
            raise PluginCapabilityError(f"跳转目标不是 http/https：{parsed.scheme!r}")
        if not host_within_limits(parsed.hostname or "", self._hosts):
            raise PluginCapabilityError(f"跳转到域名 {parsed.hostname!r}（不在放行范围里）")

        return super().redirect_request(req, fp, code, msg, headers, newurl)


class PluginNetCapabilities:
    """``ctx.capabilities.net`` —— 网络那一族（``net.fetch``）。

    **域名必须在放行的范围里**（``net.hosts``）；只放行 http/https。
    """

    def __init__(self, granted: Sequence[str], hosts: Sequence[str]) -> None:
        self._granted = frozenset(granted)
        self._hosts = list(hosts)

    def fetch(self, url: str, *, timeout: float = 30.0) -> PluginHttpResponse:
        """取一个放行范围内域名的 URL（只给数据，不给连接本身）。"""
        if "net.fetch" not in self._granted:
            raise PluginCapabilityError(
                f"能力 'net.fetch' 未放行（这个插件被放行的：{', '.join(sorted(self._granted)) or '（没有）'}）"
            )

        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https"):
            raise PluginCapabilityError(f"只放行 http/https，收到 {parsed.scheme!r}")
        if not host_within_limits(parsed.hostname or "", self._hosts):
            raise PluginCapabilityError(
                f"域名 {parsed.hostname!r} 不在放行范围里（放行的：{', '.join(self._hosts) or '（没有）'}）"
            )

        opener = urllib.request.build_opener(_HostCheckedRedirectHandler(self._hosts))
        request = urllib.request.Request(url, headers={"User-Agent": "Plobi-plugin"})

        try:
            with opener.open(request, timeout=timeout) as response:
                return PluginHttpResponse(
                    status=response.status,
                    body=response.read(),
                    headers={key: value for key, value in response.headers.items()},
                )
        except urllib.error.HTTPError as exc:
            return PluginHttpResponse(
                status=exc.code,
                body=exc.read(),
                headers={key: value for key, value in (exc.headers or {}).items()},
            )
        except PluginCapabilityError:
            raise
        except Exception as exc:
            raise PluginCapabilityError(f"取不到 {url!r}：{exc}") from exc


# ── 宿主注入的那一个对象 ──────────────────────────────────────────────────────


class PluginCapabilities:
    """``ctx.capabilities`` —— **宿主注入的窄接口**（规格 §2.2）。

    **没被放行的能力连属性都没有** ⇒ 插件拿不到句柄。
    """

    def __init__(self, granted: Sequence[str], limits: Mapping[str, Any]) -> None:
        self.__dict__["_granted"] = frozenset(granted)
        self.__dict__["_limits"] = dict(limits or {})

        if any(name.startswith("fs.") for name in self._granted):
            self.__dict__["fs"] = PluginFileCapabilities(
                [name for name in self._granted if name.startswith("fs.")],
                _limit_paths(self._limits),
            )
        if any(name.startswith("net.") for name in self._granted):
            self.__dict__["net"] = PluginNetCapabilities(
                [name for name in self._granted if name.startswith("net.")],
                _limit_hosts(self._limits),
            )

    @property
    def granted(self) -> Tuple[str, ...]:
        """这个插件**被放行**的能力（读出来给人看的那一份）。"""
        return tuple(sorted(self.__dict__["_granted"]))

    def __getattr__(self, item: str) -> Any:
        # 只有「属性不存在」才会走到这里 ⇒ 那就是「这项能力没放行」。
        granted = self.__dict__.get("_granted", ())
        raise PluginCapabilityError(
            f"能力 {item!r} 未放行（这个插件被放行的：{', '.join(sorted(granted)) or '（没有）'}）"
        )


def build_plugin_capabilities(manifest: Any) -> PluginCapabilities:
    """按 manifest 的**声明**与**边界**造出那个窄接口（规格 §2.1／§2.2）。

    **放行＝「声明了」且「有边界」**：

    - 没声明的能力 ⇒ 不放行（插件压根拿不到句柄）；
    - 声明了但**缺边界**（``net.fetch`` 没有 ``net.hosts``／``fs.*`` 没有 ``fs.paths``）
      ⇒ **不放行**（默认拒绝，不是不设限）；
    - 用户逐项放行／撤销那一层是**刀 4**（同意界面）——那时「放行」还要再过用户那一关。
    """
    declared = [str(name) for name in (getattr(manifest, "permissions", None) or [])]
    limits = dict(getattr(manifest, "limits", None) or {})

    granted = [
        name
        for name in declared
        if name.startswith(("fs.", "net."))
        and capability_grantable(name, limits)
    ]

    return PluginCapabilities(granted, limits)
