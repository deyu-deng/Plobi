"""Plobi-owned quota source pool configuration (B3: quota source management).

额度池 = 便宜 API（zhipu air 档）+ aigw 聚合（antigravity / workbuddy 免费额度）。
L1 按策略挑源（便宜优先、aigw 兜底）；L2 在源失效时自动切换。

配置真源：``$HERMES_HOME/plobi/quota.yaml``（profile-safe，走 ``get_hermes_home()``，
``PLOBI_QUOTA_CONFIG`` 可覆盖）。每个源的 ``base_url`` / ``api_key`` 是敏感信息，
一律从环境变量读（``url_env`` / ``key_env``），配置文件只放非敏感字段（kind/model/
base_url 默认值）。优先级：quota.yaml > 环境变量 > credential_pool 只读桥 > 默认值。

WP-BE-2 凭证桥：UI 存 key 走 Hermes ``auth.json`` 的 ``credential_pool``（条目可以是
``env:VAR`` 引用，token 本体在 ``$HERMES_HOME/.env``），而额度池此前只认
``PLOBI_QUOTA_*`` 环境变量——两套凭证面互不相通，三源 probe 全部 unavailable。
``_resolve_source`` 在 env 未设时按 ``credential_provider`` 只读查询
``read_credential_pool``（纯读，不写盘不 seed），补齐 ``api_key`` / 空缺的
``base_url``。zhipu-air 映射 ``zai`` provider。

配置文件示例::

    version: 1
    strategy:
      order: [zhipu-air, antigravity, workbuddy]   # 便宜优先
      fail_open: true                               # 全失效时降级（不硬 block）
    sources:
      zhipu-air:
        kind: cheap_api
        model: glm-4-air
        base_url: ""          # 留空 = 从 PLOBI_QUOTA_ZHIPU_URL 读
        api_key: ""           # 留空 = 从 PLOBI_QUOTA_ZHIPU_KEY 读
      antigravity:
        kind: aigw
        model: antigravity/gemini-3-pro
        base_url: http://127.0.0.1:8000/v1   # aigw 本地网关
        api_key: ""           # 留空 = 从 PLOBI_QUOTA_AIGW_KEY 读
      workbuddy:
        kind: aigw
        model: workbuddy/deepseek-chat
        base_url: http://127.0.0.1:8000/v1
        api_key: ""
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional


# ── Defaults ─────────────────────────────────────────────────────────────────

# 三源默认结构。model 名是配置默认值，可在 quota.yaml 覆盖（不硬编码到路由）。
DEFAULT_SOURCES: dict[str, dict[str, Any]] = {
    "zhipu-air": {
        "kind": "cheap_api",
        "model": "glm-4-air",
        "base_url": "",  # 留空 → 走 PLOBI_QUOTA_ZHIPU_URL → credential_pool(zai)
        "api_key": "",  # 留空 → 走 PLOBI_QUOTA_ZHIPU_KEY → credential_pool(zai)
        "url_env": "PLOBI_QUOTA_ZHIPU_URL",
        "key_env": "PLOBI_QUOTA_ZHIPU_KEY",
        "credential_provider": "zai",  # WP-BE-2 只读桥的 provider 名
    },
    "antigravity": {
        "kind": "aigw",
        "model": "antigravity/gemini-3-pro",
        "base_url": "http://127.0.0.1:8000/v1",  # aigw 本地网关
        "api_key": "",
        "url_env": "PLOBI_QUOTA_AIGW_URL",
        "key_env": "PLOBI_QUOTA_AIGW_KEY",
    },
    "workbuddy": {
        "kind": "aigw",
        "model": "workbuddy/deepseek-chat",
        "base_url": "http://127.0.0.1:8000/v1",
        "api_key": "",
        "url_env": "PLOBI_QUOTA_AIGW_URL",
        "key_env": "PLOBI_QUOTA_AIGW_KEY",
    },
}

# 便宜优先：zhipu-air（便宜 API）→ antigravity（aigw 已验证）→ workbuddy（aigw）。
DEFAULT_ORDER: list[str] = ["zhipu-air", "antigravity", "workbuddy"]

# 预警阈值：健康度低于该档即触发预警（进早报，B5 消费）。
DEFAULT_ALERT_THRESHOLD = "degraded"

# 全源失效时的默认行为。True = 返回 None 让调用方降级（不硬 block）。
DEFAULT_FAIL_OPEN = True


def config_path() -> Path:
    """额度池配置文件路径：$HERMES_HOME/plobi/quota.yaml（profile-safe）。"""
    override = os.environ.get("PLOBI_QUOTA_CONFIG", "").strip()
    if override:
        return Path(override)
    try:
        from hermes_constants import get_hermes_home

        root = get_hermes_home() / "plobi"
    except Exception:
        root = Path.home() / ".hermes" / "plobi"
    return root / "quota.yaml"


# ── credential bridge (WP-BE-2) ──────────────────────────────────────────────


def _lookup_credential(provider: str) -> tuple[str, str]:
    """只读查询 Hermes credential_pool 中某 provider 的 ``(token, base_url)``。

    复用底座 ``hermes_cli.auth.read_credential_pool``（纯读：profile 优先 +
    全局回退，不写盘不 seed）。条目可能没有内联 token 而是 ``env:VAR`` 引用
    （UI 存 key 的形态），此时按底座同样的语义解析：``$HERMES_HOME/.env``
    （``load_env()``）优先，``os.environ`` 兜底。查不到诚实返回 ``("", "")``，
    不伪造凭据。
    """
    try:
        from hermes_cli.auth import read_credential_pool

        entries = read_credential_pool(provider)
    except Exception:
        return "", ""

    best: Optional[dict[str, Any]] = None
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        if best is None or int(entry.get("priority") or 0) > int(
            best.get("priority") or 0
        ):
            best = entry
    if best is None:
        return "", ""

    token = str(best.get("access_token") or "").strip()
    if not token:
        source = str(best.get("source") or "").strip()
        if source.startswith("env:"):
            var = source[len("env:"):].strip()
            dotenv: dict[str, str] = {}
            try:
                from hermes_cli.config import load_env

                dotenv = load_env() or {}
            except Exception:
                dotenv = {}
            token = (dotenv.get(var) or os.environ.get(var) or "").strip()

    url = str(best.get("inference_base_url") or best.get("base_url") or "").strip()
    return token, url


def _read_file(path: Path) -> dict[str, Any]:
    try:
        import yaml

        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _resolve_source(
    name: str,
    file_cfg: dict[str, Any],
    env: dict[str, str],
) -> dict[str, Any]:
    """合并一个源的配置：文件 > 环境变量 > 默认值。"""
    default = DEFAULT_SOURCES.get(name, {})
    merged: dict[str, Any] = dict(default)
    # 文件覆盖非敏感字段
    merged.update({k: v for k, v in file_cfg.items() if v not in ("", None)})
    # 环境变量覆盖 url / key（敏感信息不进配置文件）
    url_env = merged.get("url_env", "")
    key_env = merged.get("key_env", "")
    if url_env and env.get(url_env):
        merged["base_url"] = env[url_env]
    if key_env and env.get(key_env):
        merged["api_key"] = env[key_env]
    # WP-BE-2 凭证桥：env 未设时只读回退 auth.json credential_pool（不覆盖已解析值）。
    if not merged.get("api_key"):
        cp = str(merged.get("credential_provider") or "").strip()
        if cp:
            token, pool_url = _lookup_credential(cp)
            if token:
                merged["api_key"] = token
            if pool_url and not merged.get("base_url"):
                merged["base_url"] = pool_url
    # kind / model 缺省兜底
    merged.setdefault("kind", default.get("kind", "aigw"))
    merged.setdefault("model", default.get("model", ""))
    return merged


def load() -> dict[str, Any]:
    """解析整个额度池配置，返回 ``{order, fail_open, alert_threshold, sources}``。

    ``sources`` 是一个 ``{name: {kind, model, base_url, api_key}}`` 映射，其中
    ``base_url`` / ``api_key`` 已按「文件 > env > 默认」解析完成。
    """
    path = config_path()
    file_cfg = _read_file(path)

    strategy = file_cfg.get("strategy") if isinstance(file_cfg.get("strategy"), dict) else {}
    order = strategy.get("order") or DEFAULT_ORDER
    if not isinstance(order, list) or not order:
        order = DEFAULT_ORDER
    order = [str(n) for n in order]

    fail_open = strategy.get("fail_open", DEFAULT_FAIL_OPEN)
    if not isinstance(fail_open, bool):
        fail_open = DEFAULT_FAIL_OPEN

    raw_sources = file_cfg.get("sources") if isinstance(file_cfg.get("sources"), dict) else {}
    env = dict(os.environ)
    sources: dict[str, dict[str, Any]] = {}
    for name in DEFAULT_SOURCES:
        sources[name] = _resolve_source(
            name,
            raw_sources.get(name) if isinstance(raw_sources.get(name), dict) else {},
            env,
        )

    alert_threshold = file_cfg.get("alert_threshold", DEFAULT_ALERT_THRESHOLD)
    if not isinstance(alert_threshold, str) or not alert_threshold:
        alert_threshold = DEFAULT_ALERT_THRESHOLD

    return {
        "order": order,
        "fail_open": fail_open,
        "alert_threshold": alert_threshold,
        "sources": sources,
    }


def load_source(name: str) -> Optional[dict[str, Any]]:
    """加载单个源的配置，未知源返回 None。"""
    return load()["sources"].get(name)
