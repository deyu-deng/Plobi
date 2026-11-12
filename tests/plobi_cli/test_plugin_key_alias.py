"""裁定 217：插件键改名后的存量 config 兼容。

新插件键是 ``plobi-core``；已装机用户的 ``plugins.enabled`` / ``disabled``
里仍写着 ``plobi-north-star``。加载器在读取配置时把旧键折成新键，
**不回写用户配置**（安装位只读，见 dev-workflow.md 附录 A §3.7）。
"""

from __future__ import annotations

import io
from pathlib import Path

from plobi_cli.plugins import _LEGACY_PLUGIN_KEYS, _normalize_plugin_keys

REPO = Path(__file__).resolve().parents[2]


def test_legacy_key_folds_to_core():
    assert _normalize_plugin_keys(["plobi-north-star", "memory"]) == {
        "plobi-core", "memory",
    }
    assert _normalize_plugin_keys({"plobi_north_star"}) == {"plobi_core"}
    assert _LEGACY_PLUGIN_KEYS["plobi-north-star"] == "plobi-core"


def test_none_keeps_opt_in_semantics():
    # None 表示「默认什么都不启用」；归一化不许把它变成空集合。
    assert _normalize_plugin_keys(None) is None


def test_unknown_keys_are_untouched():
    assert _normalize_plugin_keys(["category/thing", "memory", "tts/openai"]) == {
        "category/thing", "memory", "tts/openai",
    }


def test_bundled_manifest_uses_new_key():
    manifest = REPO / "plugins" / "plobi-core" / "plugin.yaml"
    text = io.open(manifest, encoding="utf-8").read()
    assert text.splitlines()[0].strip() == "name: plobi-core"


def test_legacy_route_key_is_deliberately_kept():
    """HTTP 路由键按兼容保留，别被下一次「清扫旧名」顺手改掉。"""
    manifest = REPO / "plugins" / "plobi-core" / "dashboard" / "manifest.json"
    assert '"name": "plobi-north-star"' in io.open(manifest, encoding="utf-8").read()
    client = REPO / "apps" / "desktop" / "src" / "lib" / "plobi-api.ts"
    assert "'/api/plugins/plobi-north-star'" in io.open(client, encoding="utf-8").read()
