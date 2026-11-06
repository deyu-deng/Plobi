"""WP-PROFILE-SCOPE-ROSTER —— 作用域按名册行解析：存在性问**名册**，不问家目录。

用户 10-26 截图报「桌面启动失败 · Profile 'srtp' does not exist.」。

病根：后端读面三处（``GET /api/sessions``／``GET /api/agents/sessions``／
``GET /api/sessions/{id}``）拿「家目录在不在盘上」当作用域存在性的判据，而 10-24 隔离
把 23 具旧家搬进了 ``_quarantine-20261024/``——名册那 24 行还在、家没了 ⇒ 读面 404 ⇒
桌面冷启动那条**没有 catch** 的 ``refreshSessions()`` 把它变成整台开机失败。

判据七条（纸 §四）：

  ① 名册里有的作用域**不再** 404；
  ② 圈出来的**真是那一行**的会话（不是全库）；
  ③ 查无此行仍然 404（正文一字不变）／语法错仍然 400；
  ④ ``default`` 与 ``all`` 两条老路一字不变；
  ⑤ **一个字节都不重建**（盘上不冒出 ``profiles/<name>/``）；
  ⑥ 桌面侧：列表失败**不**打死开机（源码口径，见 ``use-gateway-boot.ts``）；
  ⑦ 不新增红（跑既有那几族 ＋ 基线核对）。
"""

from __future__ import annotations

import pytest

from plobi.agents.registry import AgentEntry, AgentRegistry
from plobi.agents.registry import default_path as registry_default_path


@pytest.fixture()
def roster(_isolate_plobi_home):
    """名册写共享层那一份（``default_path()`` = ``<root>/plobi/projects.yaml``）。"""
    path = registry_default_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    reg = AgentRegistry(path)
    yield reg
    reg.save()


def _add(reg: AgentRegistry, name: str) -> None:
    reg.upsert(AgentEntry(name=name, role="l2_agenda"))
    reg.save()


def _scope(profile):
    from plobi_cli.web_server import _scope_name_for_read

    return _scope_name_for_read(profile)


@pytest.fixture()
def client(_isolate_plobi_home, monkeypatch):
    """真端点那一路（读面三处就是它接的）。"""
    try:
        from starlette.testclient import TestClient
    except ImportError:
        pytest.skip("fastapi/starlette not installed")

    import plobi_state
    from plobi_constants import get_plobi_home
    from plobi_cli.web_server import app, _SESSION_HEADER_NAME, _SESSION_TOKEN

    monkeypatch.setattr(plobi_state, "DEFAULT_DB_PATH", get_plobi_home() / "state.db")

    test_client = TestClient(app)
    test_client.headers[_SESSION_HEADER_NAME] = _SESSION_TOKEN
    return test_client


def _seed_session(session_id: str, agent_id: str) -> None:
    from plobi_state import SessionDB

    db = SessionDB()
    try:
        db.create_session(session_id=session_id, source="cli", agent_id=agent_id)
    finally:
        db.close()


# ── ① 名册里有的作用域不再 404 ─────────────────────────────────────────────


def test_roster_scope_is_not_missing(roster):
    """① 名册里有的那一行 ⇒ 不是「不存在」。"""
    _add(roster, "srtp")

    assert _scope("srtp") == "srtp"


def test_roster_scope_matches_by_profile_name_not_by_dir(roster):
    """① 旧的家名写法（``l2-agenda``）照样归到名册 name —— 别名反查还在。"""
    _add(roster, "agenda")

    assert _scope("l2-agenda") == "agenda"
    assert _scope("agenda") == "agenda"


# ── ② 圈出来的真是那一行的会话 ─────────────────────────────────────────────


def test_scoped_list_returns_only_that_rows_sessions(client, roster):
    """② 名册行的作用域 ⇒ 只圈那一行的会话，不是全库。"""
    _add(roster, "srtp")
    _seed_session("row-of-srtp", "srtp")
    _seed_session("row-of-other", "other-agent")

    resp = client.get("/api/agents/sessions?agent=srtp&limit=50&offset=0")

    assert resp.status_code == 200, resp.text
    ids = {row["id"] for row in resp.json()["sessions"]}
    assert "row-of-srtp" in ids
    assert "row-of-other" not in ids


# ── ③ 坏名字仍然 404／400，正文一字不变 ───────────────────────────────────


def test_unknown_scope_still_404s_with_the_same_wording(roster):
    """③ 查无此行 ⇒ 404，正文仍是那句 ``Profile 'x' does not exist.``。"""
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        _scope("no-such-row")

    assert exc.value.status_code == 404
    assert exc.value.detail == "Profile 'no-such-row' does not exist."


def test_invalid_scope_is_still_400(roster):
    """③ 语法错仍然 400（那是另一条判据，不许被这一刀吞掉）。"""
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        _scope("bad name!")

    assert exc.value.status_code == 400


def test_endpoint_still_404s_for_an_unknown_scope(client, roster):
    """③ 端点那一路也是同一个 404、同一句正文。"""
    resp = client.get("/api/agents/sessions?agent=no-such-row")

    assert resp.status_code == 404
    assert resp.json()["detail"] == "Profile 'no-such-row' does not exist."


# ── ④ default 与 all 两条老路一字不变 ──────────────────────────────────────


def test_default_scope_is_unchanged(roster):
    """④ ``default`` 是 L1 那个伪作用域，永远算数。"""
    assert _scope("default") == "default"
    assert _scope(None) == "default"


def test_all_and_default_still_answer_on_the_endpoint(client, roster):
    """④ ``profile=all`` 与 ``profile=default`` 都还是 200。"""
    assert client.get("/api/agents/sessions?agent=all").status_code == 200
    assert client.get("/api/agents/sessions?agent=default").status_code == 200


# ── ⑤ 一个字节都不重建 ─────────────────────────────────────────────────────


def test_resolving_a_roster_scope_creates_no_home(roster):
    """⑤ 修法是**换判据**，不是把家造回来。"""
    from plobi_constants import get_plobi_home

    root = get_plobi_home()
    _add(roster, "srtp")

    assert _scope("srtp") == "srtp"

    assert not (root / "profiles" / "srtp").exists(), "又建了一个分身家"
    assert not (root / "profiles").exists() or not any((root / "profiles").iterdir())


def test_a_home_that_is_still_on_disk_still_resolves(roster):
    """⑤ 反面也要钉住：家**还在**盘上的（``coder`` 那具空壳）照旧能用。"""
    from plobi_constants import get_plobi_home

    (get_plobi_home() / "profiles" / "coder").mkdir(parents=True, exist_ok=True)

    assert _scope("coder") == "coder"


# ── 端点那一路：读面三处都换成问名册 ───────────────────────────────────────


def test_sessions_endpoint_accepts_a_roster_scope(client, roster):
    """``GET /api/sessions`` 那一处也是问名册了。"""
    _add(roster, "srtp")

    resp = client.get("/api/sessions?profile=srtp&limit=20&offset=0")

    assert resp.status_code == 200, resp.text


def test_session_detail_endpoint_accepts_a_roster_scope(client, roster):
    """``GET /api/sessions/{id}`` 那一处也是问名册了。"""
    _add(roster, "srtp")
    _seed_session("detail-row", "srtp")

    resp = client.get("/api/sessions/detail-row?profile=srtp")

    assert resp.status_code == 200, resp.text
    assert resp.json()["profile"] == "srtp"
