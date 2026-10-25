"""Subscription import: pull (or paste) somebody else's node list and **unfold it into the table**.

Ruled shape (用户 10-16 拍的甲)：点一下才拉，拉回来的东西**解析成 `network.egress.nodes`/`groups`**，
一张表还是唯一真源 —— 面板看得见每一个成员、规则引用得到组、`plobi network status` 说的就是它在服务的
东西。第三条路（把订阅 URL 交给 mihomo 的 `proxy-providers`）改动最小，但那等于让引擎自己持有一份
成员清单：面板看不到、规则引用不到、status 读数不再等于实际 ⇒ 与裁定 94/145 冲突，这里不做。

四件事是本模块的边界，每条都有代码而不只是文档：

* **凭据不进表**：链接里 `ss://`/`vmess://` 这类把密码写在字符串里，导入时把它们逐个落成
  `PLOBI_EGRESS_*` 环境变量（走既有的 `save_env_value_secure`，`.env` 才是值的家），表里只留 `*_env` 名字。
  返回值里也**只有名字**——没有哪个调用方能从结果里读到值。
* **只有一条读代理的路**：拉订阅这件事自己也要出门，用的是
  `agent.process_bootstrap._get_proxy_for_base_url`（裁定 101 判据① 点名要复用的那一条），
  不在这里再写一份「读 `HTTPS_PROXY`」。
* **失败就停，不静默重试**：拉不到就报「网络不可达」并带上是哪一步、什么原因。这台的前例是一次静默重试
  跑完烧掉 114,124 input tokens、产物目录是空的。
* **不认识的格式点名拒**：sing-box 的 `outbounds`、`load-balance` 这类，说明为什么没收下，而不是
  悄悄产出一堆残缺成员。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

MAX_BYTES = 4_000_000
TIMEOUT_SECONDS = 20.0
ENV_PREFIX = "PLOBI_EGRESS"

#: mihomo/Clash 的成员类型 ↔ 表里的类型（表里那份 `_NODE_TYPES` 是唯一口径，这里只做别名映射）
_TYPE_ALIAS = {
    "ss": "ss", "shadowsocks": "ss", "ssr": "ss",
    "vmess": "vmess", "trojan": "trojan", "vless": "vless",
    "socks": "socks5", "socks5": "socks5", "http": "http", "https": "http",
}
#: Which field carries the secret, per table type. `socks5`/`http` do have one (`rules.py` lets any
#: node declare `password_env`/`username_env`), and the pane renders a credential slot for them —
#: leaving them out here would silently drop the password off a `socks5://user:pass@host` link.
_SECRET_FOR_TYPE = {"ss": "password", "vmess": "uuid", "vless": "uuid", "trojan": "password",
                    "socks5": "password", "http": "password"}
_GROUP_ALIAS = {"url-test": "url-test", "select": "select", "fallback": "fallback"}
_BUILTIN_POLICIES = {"direct", "reject", "pass", "dns", "compat", "sniff", "load-balance"}


class SubscriptionError(Exception):
    """A refusal the user can act on: the reason is in the message, never in a traceback."""


def _b64url_decode(raw: str) -> str:
    padded = raw + "=" * (-len(raw) % 4)
    for decoder in (base64.urlsafe_b64decode, base64.b64decode):
        try:
            return decoder(padded.encode("utf-8")).decode("utf-8", "replace")
        except (binascii.Error, ValueError):
            continue
    return raw


def _looks_like_b64_blob(text: str) -> bool:
    body = "".join(text.split())
    return 24 <= len(body) <= MAX_BYTES and body.isascii() and bool(re.fullmatch(r"[A-Za-z0-9+/=_\-]+", body))


def unwrap(body: str) -> tuple[str, list[str]]:
    """Subscription bodies come in three wrappings; normalise to text and say which one we saw."""
    notes: list[str] = []
    text = (body or "").strip()
    if not text:
        raise SubscriptionError("订阅内容是空的——没东西可解析")
    if _looks_like_b64_blob(text):
        decoded = _b64url_decode(text)
        if decoded and decoded != text and ("\n" in decoded or ":" in decoded):
            notes.append("内容是 base64 包装，已展开")
            return decoded.strip(), notes
    return text, notes


def _name_from(uri: str, fallback: str) -> str:
    frag = urllib.parse.urlparse(uri).fragment
    if frag:
        return urllib.parse.unquote(frag)
    query = urllib.parse.parse_qs(urllib.parse.urlparse(uri).query)
    for key in ("remarks", "ps", "name"):
        if query.get(key):
            return query[key][0]
    return fallback


def _node(name: str, kind: str, server: str, port: int, *, fields: dict[str, Any],
          secret: str = "") -> dict[str, Any]:
    return {"name": name, "type": kind, "server": server, "port": port, "fields": fields,
            "secret": secret, "secret_field": _SECRET_FOR_TYPE.get(kind, "")}


def decode_link(uri: str, *, index: int) -> dict[str, Any]:
    """One `ss://`/`trojan://`/`vmess://`/`vless://`/`socks5://` line → a node shape."""
    scheme, _, rest = uri.partition("://")
    scheme = _TYPE_ALIAS.get((scheme or "").strip().lower(), "")
    if not scheme:
        raise SubscriptionError(f"第 {index} 行的链接类型 {uri.split('://')[0]!r} 不认识")
    if scheme == "vmess":
        payload = json.loads(_b64url_decode(rest))
        # vmess puts the *server* in `add` and uses `host` for the WebSocket Host header —
        # reading `host` as the address silently points every ws node at some CDN hostname.
        host, port = str(payload.get("add") or ""), int(payload.get("port") or 0)
        return _node(str(payload.get("ps") or f"node-{index}"), "vmess", host, port,
                     fields={k: v for k, v in payload.items()
                             if k in ("alterId", "cipher", "tls", "servername", "network", "path", "host")},
                     secret=str(payload.get("id") or ""))
    parsed = urllib.parse.urlparse(uri)
    host = parsed.hostname or ""
    port = parsed.port or 0
    user = urllib.parse.unquote(parsed.username or "")
    pwd = urllib.parse.unquote(parsed.password or "")
    fields = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()
              if k in ("type", "security", "tls", "sni", "servername", "flow", "network", "path",
                       "host", "alterId", "cipher", "udp", "skip-cert-verify", "alpn", "insecure")}
    if scheme == "ss":
        # SIP002 says the userinfo is either `method:password` (percent-encoded) or
        # base64(`method:password`) — both shapes are out in the wild, and getting this wrong
        # turns a whole subscription into "缺 cipher".
        cipher, secret = "", ""
        if pwd:
            cipher, secret = user, pwd
        else:
            decoded = _b64url_decode(user)
            if ":" in decoded:
                cipher, secret = decoded.split(":", 1)
            elif ":" in user:
                cipher, secret = user.split(":", 1)
        if not cipher or not secret:
            raise SubscriptionError(f"ss 链接第 {index} 行解不出 cipher+password"
                                    f"（两种形状都试过：ss://method:password@host 与 ss://base64(method:password)@host）")
        fields.setdefault("cipher", cipher)
        return _node(_name_from(uri, f"node-{index}"), "ss", host, port, fields=fields, secret=secret)
    if scheme in ("socks5", "http"):
        fields_out = dict(fields)
        if user:
            fields_out["username"] = user
        return _node(_name_from(uri, f"node-{index}"), scheme, host, port, fields=fields_out, secret=pwd)
    return _node(_name_from(uri, f"node-{index}"), scheme, host, port, fields=fields, secret=user or pwd)


def _from_yaml(doc: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    nodes, groups, notes = [], [], []
    for raw in doc.get("proxies") or []:
        if not isinstance(raw, dict):
            notes.append("yaml 里有一条 proxy 不是映射，跳过了")
            continue
        kind = _TYPE_ALIAS.get(str(raw.get("type", "")).strip().lower(), "")
        if not kind:
            notes.append(f"成员 {raw.get('name', '?')} 的类型 {raw.get('type')!r} 表里没有对应，未导入")
            continue
        fields = {k: v for k, v in raw.items()
                  if k not in ("name", "type", "server", "port", "password", "uuid", "username")}
        secret = str(raw.get("uuid") or raw.get("password") or "")
        nodes.append(_node(str(raw.get("name") or f"node-{len(nodes) + 1}"), kind,
                           str(raw.get("server") or ""), int(raw.get("port") or 0),
                           fields=fields, secret=secret))
    names = {n["name"] for n in nodes}
    for raw in doc.get("proxy-groups") or []:
        if not isinstance(raw, dict):
            continue
        kind = _GROUP_ALIAS.get(str(raw.get("type", "")).strip().lower(), "")
        if not kind:
            notes.append(f"组 {raw.get('name')!r} 的类型 {raw.get('type')!r} 表里没有对应（如 load-balance），未导入")
            continue
        members = [str(m) for m in (raw.get("proxies") or []) if str(m) in names]
        dropped = [str(m) for m in (raw.get("proxies") or []) if str(m) not in names]
        if dropped:
            notes.append(f"组 {raw.get('name')!r} 引用的 {', '.join(map(str, dropped[:4]))} 不在本次成员里，已去掉（否则解析器会整段拒）")
        if not members:
            notes.append(f"组 {raw.get('name')!r} 去掉引用后没有成员，未导入")
            continue
        groups.append({"name": str(raw.get("name")), "type": kind, "include": members,
                       "url": str(raw.get("url") or ""), "interval": int(raw.get("interval") or 0)})
    return nodes, groups, notes


def parse(text: str) -> dict[str, Any]:
    """Subscription body → {format, nodes, groups, notes}. Values stay in `nodes[i]['secret']`
    only until :func:`attach_env_names` moves them into the root `.env`."""
    body, notes = unwrap(text)
    nodes: list[dict[str, Any]] = []
    groups: list[dict[str, Any]] = []
    fmt = ""

    # sing-box says `outbounds`; it has to be recognised **before** the Clash branch, otherwise a
    # sing-box file half-parses into garbage members instead of one refusal the user can act on.
    if re.search(r"^\s*(outbounds|inbounds|route)\s*[:{]|\{\s*\"outbounds\"", body, re.M):
        raise SubscriptionError("这是 sing-box 的配置（成员在 `outbounds` 里，字段语义与 mihomo 不同）——"
                                "本表只收 Clash/mihomo 那两种形状：一份 yaml，或一份 ss:// / vmess:// 链接清单。"
                                "机场一般同屏给两种链接，换成 Clash 那条就行")

    if re.search(r"^\s*(proxies|proxy-providers|proxy-groups)\s*:", body, re.M):
        import yaml

        doc = yaml.safe_load(body)
        if not isinstance(doc, dict):
            raise SubscriptionError("yaml 解析出来不是映射，没法当配置收")
        if doc.get("outbounds"):
            raise SubscriptionError("这是 sing-box 的配置：它的成员在 outbounds 里，语义与 mihomo 不同，"
                                    "本表只收 mihomo/Clash 那两种形状（yaml 或链接清单）——要用请把订阅换成 Clash 格式")
        fmt = "clash-yaml"
        nodes, groups, note2 = _from_yaml(doc)
        notes += note2
    else:
        lines = [l.strip() for l in body.splitlines() if l.strip() and not l.strip().startswith("#")]
        if not any("://" in l for l in lines):
            raise SubscriptionError("既不是 Clash 的 yaml，也不是 ss:// / vmess:// 这类链接清单——"
                                    "这份内容我认不出来，不敢半收")
        fmt = "link-list"
        for i, line in enumerate(lines, 1):
            nodes.append(decode_link(line, index=i))
    clean: list[dict[str, Any]] = []
    seen: set[str] = set()
    for node in nodes:
        if not node["name"] or not node["server"] or not node["port"]:
            notes.append(f"成员 {node.get('name') or '?'} 缺 server/port，未导入")
            continue
        if node["name"] in seen:
            notes.append(f"重名成员 {node['name']} 只留第一个（表里名字是键）")
            continue
        seen.add(node["name"])
        clean.append(node)
    return {"format": fmt, "nodes": clean, "groups": groups, "notes": notes}


def _env_name(node_name: str, field: str, port: int) -> str:
    """Deterministic, stable across re-imports, and readable enough to debug from `.env`.

    Measured on a real ikuuu subscription (10-25): names are `🇭🇰 香港W01`-style, so an
    ASCII-only slug collapses to whatever Latin letters survive in the middle of the CJK name —
    `PLOBI_EGRESS_W01_19272_PASSWORD`, which is worse than useless when three nodes all end in "W01".
    A short hash of the *whole* name keeps it unique and stable; the port still carries the real
    disambiguation, and nodes with Latin names keep the readable slug.
    """
    slug = re.sub(r"[^A-Za-z0-9]+", "_", node_name).strip("_").upper()
    if len(slug) < 3 or re.search(r"[^\x00-\x7f]", node_name):
        # Any non-ASCII in the name means the slug is lossy by construction (`🇭🇰 香港W01` → `W01`),
        # so prefer an honest hash over a half-name that looks like it identifies something.
        slug = "N" + hashlib.sha1(node_name.encode("utf-8")).hexdigest()[:8].upper()
    return f"{ENV_PREFIX}_{slug}_{port}_{field.upper()}"


def attach_env_names(nodes: list[dict[str, Any]], *, write: bool = True) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """Move every credential out of the returned table shape and into the root `.env`.

    Returns `(table_nodes, env_names_written, notes)`. **The values are never in the return value** —
    a caller that logs `table_nodes` cannot leak a password, which is the whole point of the `*_env`
    shape (rules.py:431).
    """
    from plobi_cli.config import save_env_value_secure

    out, written, notes = [], [], []
    used: dict[str, int] = {}
    for node in nodes:
        entry: dict[str, Any] = {"name": node["name"], "type": node["type"],
                                 "server": node["server"], "port": node["port"]}
        entry.update({k: v for k, v in node["fields"].items() if v not in (None, "")})
        if node["secret"] and node["secret_field"]:
            base = _env_name(node["name"], node["secret_field"], node["port"])
            n = used.get(base, 0)
            name = base if n == 0 else f"{base}_{n + 1}"
            used[base] = n + 1
            if write:
                save_env_value_secure(name, node["secret"])
            written.append(name)
            entry[f"{node['secret_field']}_env"] = name
        elif node["secret"]:
            notes.append(f"成员 {node['name']} 带了凭据但类型 {node['type']} 没有对应字段，未写入 .env")
        if node["fields"].get("username"):
            entry["username_env"] = _env_name(node["name"], "username", node["port"])
            if write:
                save_env_value_secure(entry["username_env"], str(node["fields"].pop("username")))
            written.append(entry["username_env"])
        else:
            node["fields"].pop("username", None)
        out.append(entry)
    return out, written, notes


def fetch(url: str, *, timeout: float = TIMEOUT_SECONDS) -> str:
    """One pull, through the one proxy reader. Refuses loudly; never retries."""
    from agent.process_bootstrap import _get_proxy_for_base_url

    if not re.match(r"^https?://", url or ""):
        raise SubscriptionError("订阅链接要是 http(s):// 开头——文件路径那种走「导入池子」那条按钮")
    handlers: list[Any] = []
    proxy = _get_proxy_for_base_url(url)
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    opener = urllib.request.build_opener(*handlers)
    request = urllib.request.Request(url, headers={"User-Agent": "clash"}, method="GET")
    try:
        with opener.open(request, timeout=timeout) as response:
            payload = response.read(MAX_BYTES + 1)
    except Exception as exc:  # noqa: BLE001 — 任何一层失败都要变成一句人话，不许往上抛栈
        raise SubscriptionError(
            f"网络不可达：拉订阅失败（{type(exc).__name__}: {str(exc)[:120]}）。"
            f"出口是 {proxy or '直连'}——不在这里重试，先看 `plobi network status` 那条腿在不在"
        ) from exc
    if len(payload) > MAX_BYTES:
        raise SubscriptionError(f"订阅超过 {MAX_BYTES} 字节，不像一份节点清单，拒收")
    return payload.decode("utf-8", "replace")


def update(*, url: str = "", text: str = "", write_secrets: bool = True) -> dict[str, Any]:
    """Pull-or-paste → parse → table-shaped entries. Never returns a credential value."""
    body = text or (fetch(url) if url else "")
    if not body:
        raise SubscriptionError("给我一段订阅链接（url）或一段配置文本（text），只有一个空的不算导入")
    parsed = parse(body)
    nodes, written, notes = attach_env_names(parsed["nodes"], write=write_secrets)
    notes = parsed["notes"] + notes
    return {
        "ok": bool(nodes),
        "format": parsed["format"],
        "source": url or "（粘贴的文本）",
        "nodes": nodes,
        "groups": parsed["groups"],
        "secret_env_names": written,
        "notes": notes,
    }
