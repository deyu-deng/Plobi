"""门自己的测试：它数出来的东西必须准，否则第三次就没人看它红了（R-050）。

这一档存在的一条理由是把"缺句子"变成会出声的门；另一条同等重要的理由是**别冤枉人**：
10-26 那把一次性脚本拿正则全文扫 `key: value`，把英文句子里的 `obtained:` 当成一个键，
报出一堆假缺口。所以门读目录必须是行首锚定、读代码必须走 AST。
"""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("check_status_copy_coverage",
                                               ROOT / "scripts" / "check_status_copy_coverage.py")
gate = importlib.util.module_from_spec(_spec)
sys.modules["check_status_copy_coverage"] = gate
_spec.loader.exec_module(gate)

API = """
from plobi.collectors.liveness import read_pool_liveness

_ERROR_STATES = frozenset({
    "auth_failed", "network_unreachable",
})
_CONNECTED_STATES = frozenset({"live", "ready"})


def status_of(*, enabled, probe=None):
    if probe is not None and probe.get("ready") is False:
        return "disconnected", str(probe.get("code"))
    return "connected", "live"


def try_source():
    return {"ok": True}
"""

COLLECTOR = """
STATE_KEY_MISSING = "key_missing"
STATE_READY = "ready"
PROBE_CODES = ("key_missing", "ready")


def probe_broken():
    return {"ready": False, "code": "store_missing", "detail": "库不在"}


def probe_ok():
    return {"ready": True, "code": "store_found", "detail": "库在位"}
"""


def _tree(tmp_path: Path, langs: dict[str, str]):
    api = tmp_path / "sources_api.py"
    api.write_text(API, encoding="utf-8")
    pkg = tmp_path / "collectors" / "fake"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "probe.py").write_text(COLLECTOR, encoding="utf-8")
    i18n = tmp_path / "i18n"
    i18n.mkdir()
    for lang, body in langs.items():
        (i18n / f"{lang}.ts").write_text(body, encoding="utf-8")
    return api, tmp_path / "collectors", i18n


def _catalog(**sentences) -> str:
    lines = "\n".join(f"            {k}: '{v}'," for k, v in sentences.items())
    return (
        "export default {\n  settings: {\n    sources: {\n"
        "      reasons: {\n" + lines + "\n      },\n"
        "      title: 'Sources',\n    },\n  },\n}\n"
    )


ALL_GOOD = dict(live="还在进新条目。", ready="前提齐了。", auth_failed="登录没过去，重填账密。",
                network_unreachable="连不上，先查网络。", key_missing="还没拿到密钥，等它自己取。")


class TestWhatCountsAsScreenBound:
    def test_probe_codes_are_in_but_only_the_failing_branch(self, tmp_path) -> None:
        api, cols, i18n = _tree(tmp_path, {l: _catalog(**ALL_GOOD) for l in gate.LANGS})

        codes = gate.screen_codes(api, cols)

        assert "store_missing" in codes, "探针说前提不成立时那一格就是屏幕上的原因码"
        assert "store_found" not in codes, "ready:True 的码不会成为 status_code，逼着写句子就是造死句子"
        assert "key_missing" in codes, "PROBE_CODES 那格字面量必须数得出来（探针回的是变量）"


class TestParsingDoesNotInventKeys:
    def test_a_colon_inside_an_english_sentence_is_not_a_key(self, tmp_path) -> None:
        """10-26 真踩过的假阳性：`... hasn't been obtained: the library is ...`。"""
        langs = {l: _catalog(**ALL_GOOD,
                             probe_failed="The WeChat data key hasn't been obtained: sealed on disk")
                for l in gate.LANGS}
        api, cols, i18n = _tree(tmp_path, langs)

        keys = gate.catalog(i18n / "zh.ts")

        assert "obtained" not in keys
        assert set(keys) == {*ALL_GOOD, "probe_failed"}


class TestItActuallyTurnsRed:
    def test_a_missing_language_is_red(self, tmp_path) -> None:
        langs = {l: _catalog(**ALL_GOOD) for l in ("zh", "ja", "zh-hant")}
        langs["en"] = _catalog(**{k: v for k, v in ALL_GOOD.items() if k != "key_missing"})
        api, cols, i18n = _tree(tmp_path, langs)

        red, _warn, _dead = gate.check(api, cols, i18n)

        assert any("key_missing" in line and "en" in line for line in red), red

    def test_a_sentence_naming_an_env_var_is_red(self, tmp_path) -> None:
        bad = dict(ALL_GOOD, key_missing="把 CHATLOG_DATA_KEY 设好再点开关。")
        api, cols, i18n = _tree(tmp_path, {l: _catalog(**bad) for l in gate.LANGS})

        red, _warn, _dead = gate.check(api, cols, i18n)

        assert any("环境变量名" in line for line in red), red

    def test_the_real_tree_is_green(self) -> None:
        """本仓现状必须是绿的：门一装就红，别人下一笔提交会被莫名拦下。"""
        red, _warn, _dead = gate.check()

        assert red == [], red
