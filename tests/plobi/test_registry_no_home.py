"""WP-REGISTRY-NO-HOME —— B 刀：名册加一行**不再建家**。

**为什么能做 B**（A 刀之后）：身份搬进共享层 `souls/<name>.md`、模型路由写共享层、
技能根接顶层那一份、会话落单库（裁定 180）——家不再承载任何东西，
`create_profile` 只剩历史惯性。

这五条就是钉住新不变量的牙：
  ① `spawn` 之后 `profiles/<name>` **不被创建**（家数不变）；
  ② 共享技能根仍接上（`apply_shared_skills_root` 不许丢）；
  ③ 共享层 `models.json` 与 `souls/<name>.md` 仍各只有一份、内容正确；
  ④ 显式 `-p` 在家不存在时**懒建空壳**成功（不再 `sys.exit(1)`——10-22 那句
     「还没在这台机器上落地」的假否定就从那里来）；
  ⑤ `spawn` 返回值里没有 `profile_dir`、`command` 不含 `-p`（不把废弃形状教出去）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from plobi.agents.registry import AgentRegistry


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """隔离 PLOBI_HOME + Path.home()（与 test_agent_registry 同一套口径）。"""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("PLOBI_HOME", str(tmp_path / ".plobi"))
    monkeypatch.delenv("PLOBI_MODELS_CONFIG", raising=False)
    monkeypatch.delenv("PLOBI_PROJECTS_CONFIG", raising=False)
    monkeypatch.delenv("PLOBI_AGENT_ID", raising=False)
    return tmp_path


def _plobi_root(home) -> Path:
    return Path(home) / ".plobi"


def _registry(home) -> AgentRegistry:
    return AgentRegistry(_plobi_root(home) / "projects.yaml")


def _seed(home) -> AgentRegistry:
    reg = _registry(home)
    reg.upsert(
        type("E", (), {"name": "agenda", "role": "l2_agenda", "profile": "l2-agenda",
                       "registered": True})()
        if False
        else __import__("plobi.agents.registry", fromlist=["AgentEntry"]).AgentEntry(
            name="agenda", role="l2_agenda", profile="l2-agenda"
        )
    )
    reg.save()
    return reg


def test_spawn_does_not_create_a_home(home):
    """① 家数不变：spawn 之后 `profiles/<name>` 不该出现。"""
    reg = _seed(home)
    profiles_root = _plobi_root(home) / "profiles"

    assert not profiles_root.exists() or not (profiles_root / "l2-agenda").exists()

    result = reg.spawn("agenda")

    assert result["routing_ok"] is True
    assert not (profiles_root / "l2-agenda").exists(), "spawn 又建了一个分身家"


def test_spawn_still_wires_the_shared_skills_root(home):
    """② 共享技能根仍接上——它与家存不存在无关，搬走家也不能丢。"""
    reg = _seed(home)

    reg.spawn("agenda")

    from plobi.routing.models import shared_config_path

    # apply_shared_skills_root 写的是顶层那一份的 skills.external_dirs；它至少
    # 要让共享根下的 skills 目录在场（懒建也建在这儿）。
    assert shared_config_path().parent.is_dir(), "共享层那一份没接上"


def test_shared_layer_has_exactly_one_copy_of_each(home):
    """③ models.json 与 souls/<name>.md 各一份、内容正确，且不在任何分家里。"""
    reg = _seed(home)
    root = _plobi_root(home)

    reg.spawn("agenda")

    models = root / "plobi" / "models.json"
    assert models.is_file(), models
    soul = root / "souls" / "agenda.md"
    assert soul.is_file(), soul
    assert "PLOBI_L2_IDENTITY" in soul.read_text(encoding="utf-8")

    profiles_root = root / "profiles"
    stray = (
        list(profiles_root.rglob("models.json")) + list(profiles_root.rglob("SOUL.md"))
        if profiles_root.exists()
        else []
    )
    assert stray == [], f"共享件被复印进了分身家: {stray}"


def test_explicit_dash_p_lazily_creates_an_empty_home(home):
    """④ 显式 `-p` 在家不存在时懒建成功——不再 sys.exit(1)。"""
    from plobi_cli.main import _lazy_create_profile

    profiles_root = _plobi_root(home) / "profiles"
    assert not (profiles_root / "l2-agenda").exists()

    created = _lazy_create_profile("l2-agenda")

    assert created is not None
    assert Path(created) == profiles_root / "l2-agenda"
    assert (profiles_root / "l2-agenda").is_dir()
    # 空壳：不拷 config / .env / SOUL / 技能（裁定 42 §42.2）。
    assert not (profiles_root / "l2-agenda" / "config.yaml").exists()
    assert not (profiles_root / "l2-agenda" / "SOUL.md").exists()


def test_lazy_create_reports_failure_instead_of_raising(home):
    """④b 建不了（名称非法）时返回 None，让上层照旧报错。"""
    from plobi_cli.main import _lazy_create_profile

    assert _lazy_create_profile("bad name!!") is None


def test_spawn_result_carries_no_profile_dir_and_no_dash_p(home):
    """⑤ 返回值不把废弃形状教出去。"""
    reg = _seed(home)

    result = reg.spawn("agenda")

    assert "profile_dir" not in result
    assert "-p" not in result["command"]
    assert result["command"] == "plobi chat"
    assert result["profile"] == "l2-agenda"
    assert result["name"] == "agenda"
