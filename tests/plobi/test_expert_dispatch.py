"""临时专家派工（WP-L3-EXPERTS 第二拍）：四类角色 + 职责前缀 + 角色侧记。"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from plobi.delegation import expert_roles  # noqa: E402
from tools import expert_tools  # noqa: E402


def test_the_four_roles_have_their_own_toolsets_and_duty_lines():
    rows = {row["key"]: row for row in expert_roles.role_rows()}

    assert set(rows) == {"file", "browser", "software", "computer"}
    assert rows["file"]["toolsets"] == ["file"]
    assert rows["browser"]["toolsets"] == ["browser", "web"]
    assert all(row["duty"] for row in rows.values())


def test_dispatch_prefixes_the_duty_line_and_narrows_the_toolsets(tmp_path, monkeypatch):
    monkeypatch.setenv(expert_tools.EXPERTS_DIR_ENV, str(tmp_path / "experts"))
    seen = {}

    def fake_delegate(**kwargs):
        seen.update(kwargs)
        return json.dumps({"results": [{"child_session_id": "kid-1", "status": "completed", "summary": "写好了"}]})

    import tools.delegate_tool as delegate

    monkeypatch.setattr(delegate, "delegate_task", fake_delegate)

    out = json.loads(expert_tools.handle(
        {"role": "file", "goal": "把三份资料折成一份汇总"},
        parent_agent=SimpleNamespace(session_id="parent-1"),
    ))

    assert out["expert"]["role"] == "file"
    assert seen["toolsets"] == ["file"], "只给它文件那一层手"
    assert "你是文件与资料专家" in seen["goal"]
    assert seen["goal"].rstrip().endswith("把三份资料折成一份汇总")
    assert out["results"][0]["child_session_id"] == "kid-1"


def test_dispatch_writes_the_role_sidecar_for_the_rail(tmp_path, monkeypatch):
    monkeypatch.setenv(expert_tools.EXPERTS_DIR_ENV, str(tmp_path / "experts"))
    import tools.delegate_tool as delegate

    monkeypatch.setattr(
        delegate, "delegate_task",
        lambda **kw: json.dumps({"results": [{"child_session_id": "kid-9", "status": "completed"}]}),
    )

    expert_tools.handle({"role": "browser", "goal": "抄下课程页的截止时间"},
                        parent_agent=SimpleNamespace(session_id="p"))

    sidecar = tmp_path / "experts" / "kid-9.json"
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    assert payload["role"] == "browser"
    assert payload["goal"] == "抄下课程页的截止时间"


def test_an_unknown_role_is_refused_with_the_four_names(monkeypatch):
    import tools.delegate_tool as delegate

    monkeypatch.setattr(delegate, "delegate_task", lambda **kw: (_ for _ in ()).throw(AssertionError("不该派")))

    out = json.loads(expert_tools.handle({"role": "god", "goal": "x"},
                                         parent_agent=SimpleNamespace(session_id="p")))

    assert out["ok"] is False and "file" in out["error"]
