"""`PUT /api/profiles/{name}/model` 现在也能**撤回**这一行的覆盖（WP-MODEL-OVERRIDE-CLEAR，用户 14:0x 拍的甲）。

三条硬规矩都在这文件里，不在注释里：

1. **全程临时家 + 临时名册**：不许拿真名册那 19 行做验收——现读它们 0 覆盖，写进去一次就回不去。
2. **两种"空"必须分开**：两键都给了、都留空 ＝ 撤回；键没给／只给一半 ＝ 4xx。
   混成一种就会把半个请求静默当成"清除成功"，那是这一格存在的理由反过来的样子。
3. 撤回之后生效值要**真的回到顶层那一份**（那一行不再带 provider/model），且**别的行逐字不动**。
"""

from __future__ import annotations

import json

import pytest
import yaml

ROW = """
agents:
  Aura:
    role: l2_project
    profile: aura
    provider: deepseek
    model: deepseek-chat
  Kit:
    role: l2_project
    profile: kit
    provider: openrouter
    model: x-top
"""


@pytest.fixture
def roster(tmp_path, monkeypatch):
    path = tmp_path / "projects.yaml"
    path.write_text(ROW, encoding="utf-8")
    monkeypatch.setattr("plobi.agents.registry.default_path", lambda: path)
    return path


@pytest.fixture
def client(monkeypatch, tmp_path, _isolate_plobi_home):
    """真路由 + 真名册写路径；只有「这个家有哪一行」这一跳按 _resolve_profile_dir 的原语义接掉。"""
    try:
        from starlette.testclient import TestClient
    except ImportError:  # pragma: no cover — 依赖缺位时这里就该红得看得见
        pytest.skip("fastapi/starlette not installed")

    import plobi_state
    from plobi_constants import get_plobi_home
    from plobi_cli import web_server
    from plobi_cli.web_server import _SESSION_HEADER_NAME, _SESSION_TOKEN, app

    monkeypatch.setattr(plobi_state, "DEFAULT_DB_PATH", get_plobi_home() / "state.db")
    # 名册行是这次要验的东西；家目录是否存在不是（且它解析到平台默认根，测试不该去那里建目录）。
    monkeypatch.setattr(web_server, "_resolve_profile_dir", lambda name: tmp_path / name)

    api = TestClient(app)
    api.headers[_SESSION_HEADER_NAME] = _SESSION_TOKEN
    return api


def _row(path, name):
    return yaml.safe_load(path.read_text(encoding="utf-8"))["agents"][name]


def test_setting_then_clearing_returns_that_row_to_the_shared_default(roster, client):
    put = client.put("/api/profiles/aura/model", json={"provider": "moonshot", "model": "k2"})

    assert put.status_code == 200, put.text
    assert put.json()["cleared"] is False
    assert _row(roster, "Aura")["provider"] == "moonshot"

    clear = client.put("/api/profiles/aura/model", json={"provider": "", "model": ""})

    assert clear.status_code == 200, clear.text
    assert clear.json()["cleared"] is True
    after = _row(roster, "Aura")
    # 这一行不再自带路由 ⇒ 读链（行覆盖 → 顶层 → 出厂默认）落回顶层那一份。
    assert not after.get("provider") and not after.get("model")
    from plobi.agents.registry import AgentRegistry

    entry = next(e for e in AgentRegistry.load().entries() if e.name == "Aura")
    assert entry.has_model_override is False


def test_a_half_given_request_is_refused_and_the_row_is_untouched(roster, client):
    before = _row(roster, "Aura")

    half = client.put("/api/profiles/aura/model", json={"provider": "", "model": "k2"})

    assert half.status_code == 400, half.text
    assert _row(roster, "Aura") == before, "半个请求被当成了清除——这条就是本格存在的理由"

    empty_body = client.put("/api/profiles/aura/model", json={})

    assert empty_body.status_code == 400, empty_body.text
    assert _row(roster, "Aura") == before, "键都没给也被当成撤回"


def test_clearing_one_row_does_not_move_any_other_row(roster, client):
    kit_before = _row(roster, "Kit")

    res = client.put("/api/profiles/aura/model", json={"provider": "", "model": ""})

    assert res.status_code == 200, res.text
    kit = _row(roster, "Kit")
    # 名册是**整份重写**的（`upsert` + `save`），实测它会给别的行补上默认键
    # （`category: butler`、`registered: true`）——那不算"动了别行"。
    # 这一格要守的是**身份与路由那四格**不许漂，所以逐格点名比，不拿整 dict 相等当判据
    # （整 dict 相等会把"补默认键"误报成改坏别行，而把默认键省掉又是另一处真源分裂）。
    for key in ("role", "profile", "provider", "model"):
        assert kit[key] == kit_before[key], key
    assert kit.get("provider") == "openrouter" and kit.get("model") == "x-top"


def test_clearing_a_row_that_had_no_override_is_idempotent(tmp_path, monkeypatch, client):
    path = tmp_path / "projects.yaml"
    path.write_text(
        yaml.safe_dump({"agents": {"Sindri": {"role": "l2_project", "profile": "sindri"}}}, sort_keys=False),
        encoding="utf-8",
    )
    monkeypatch.setattr("plobi.agents.registry.default_path", lambda: path)

    first = client.put("/api/profiles/sindri/model", json={"provider": "", "model": ""})
    second = client.put("/api/profiles/sindri/model", json={"provider": "", "model": ""})

    assert first.status_code == 200 and second.status_code == 200
    assert json.loads(json.dumps(_row(path, "Sindri")))["profile"] == "sindri"
    assert not _row(path, "Sindri").get("provider")
