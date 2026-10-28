"""临时专家的四类角色（WP-L3-EXPERTS 第二拍）：任务里给 expert，职责与工具面自动展开。

不 spawn 子智能体：只钉「预设怎么展开」与「角色侧记怎么落」这两件纯逻辑。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from plobi.delegation import expert_roles  # noqa: E402
from tools.delegate_tool import _apply_expert_presets, _expert_body  # noqa: E402


def test_the_four_roles_have_their_own_toolsets_and_duty_lines():
    rows = {row["key"]: row for row in expert_roles.role_rows()}

    assert set(rows) == {"file", "browser", "software", "computer"}
    assert rows["file"]["toolsets"] == ["file"]
    assert rows["browser"]["toolsets"] == ["browser", "web"]
    assert all(row["duty"] for row in rows.values())


def test_expert_preset_prefixes_the_duty_and_narrows_the_toolsets():
    tasks = [{"goal": "把三份资料折成一份汇总", "expert": "file"}]

    assert _apply_expert_presets(tasks) == ""
    assert "你是文件与资料专家" in tasks[0]["goal"]
    assert tasks[0]["goal"].rstrip().endswith("把三份资料折成一份汇总")
    assert tasks[0]["toolsets"] == ["file"]
    assert tasks[0]["role"] == "leaf"


def test_the_stamped_goal_still_reads_back_as_the_humans_sentence():
    tasks = [{"goal": "把三份资料折成一份汇总", "expert": "file"}]

    _apply_expert_presets(tasks)

    assert _expert_body(tasks[0]) == "把三份资料折成一份汇总"


def test_a_task_without_expert_is_untouched():
    tasks = [{"goal": "普通的一段活"}]

    assert _apply_expert_presets(tasks) == ""
    assert tasks[0] == {"goal": "普通的一段活"}


def test_an_unknown_role_is_refused_with_the_four_names():
    tasks = [{"goal": "x", "expert": "god"}]

    error = _apply_expert_presets(tasks)

    assert "file" in error and "browser" in error


def test_the_role_sidecar_is_written_for_the_rail(tmp_path, monkeypatch):
    monkeypatch.setenv("PLOBI_EXPERTS_DIR", str(tmp_path / "experts"))

    expert_roles.record_role("kid-9", "browser", "抄下课程页的截止时间")

    payload = json.loads((tmp_path / "experts" / "kid-9.json").read_text(encoding="utf-8"))
    assert payload["role"] == "browser"
    assert payload["goal"] == "抄下课程页的截止时间"
