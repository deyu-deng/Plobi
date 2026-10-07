"""裁定 42 第一刀：L2 项目分身不再复制技能，技能可见性来自顶层共享根。

三条契约（全部跑真实的 ``create_profile`` / 真实的技能扫描，断临时 HOME，
不碰 ``~/.plobi``）：

1. ``AgentRegistry.spawn`` 落地的分身上没有技能复印件，但它扫到的技能
   和顶层一模一样 —— 靠 ``create_profile(no_skills=True)`` +
   ``skills.external_dirs``（:func:`apply_shared_skills_root`）。
2. 重复 spawn 幂等：共享根只出现一次，不追加第二份。
3. 同名技能语义（``tools/skills_tool.py::_find_all_skills``）：分身自己
   目录里的技能优先，共享根里的同名项被跳过 —— 列表不会翻倍。
4. 共享根对分身的 curator 是只读外部根：分身不得改动 / 归档共享技能，
   用量记账也各自落盘（归属问题的现状，见 ``Docs`` 裁定 42 交接）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from plobi.agents.registry import (
    AgentEntry,
    AgentRegistry,
    apply_shared_skills_root,
    shared_skills_root,
)

SHARED_SKILLS = {
    # name -> (relative dir under the skills root, description)
    "alpha": ("alpha", "Alpha shared skill."),
    "beta": ("github/beta", "Beta shared skill in a category dir."),
}


@pytest.fixture()
def l2_home(tmp_path, monkeypatch):
    """A throwaway ``~/.plobi`` holding one shared skill root and two skills."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    home = tmp_path / ".plobi"
    skills = home / "skills"
    for name, (rel, _desc) in SHARED_SKILLS.items():
        d = skills / rel
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {SHARED_SKILLS[name][1]}\n---\n\n"
            f"{name} body.\n",
            encoding="utf-8",
        )
    (home / "config.yaml").write_text("model:\n  default: test-model\n", encoding="utf-8")
    (home / ".env").write_text("TEST_KEY=1\n", encoding="utf-8")
    (home / "SOUL.md").write_text("shared persona\n", encoding="utf-8")

    monkeypatch.setenv("PLOBI_HOME", str(home))
    monkeypatch.delenv("PLOBI_PROFILE", raising=False)
    monkeypatch.delenv("PLOBI_MODELS_CONFIG", raising=False)
    monkeypatch.delenv("PLOBI_PROJECTS_CONFIG", raising=False)
    _clear_skill_scanners()
    return home


@pytest.fixture()
def in_profile():
    """Run a block with ``PLOBI_HOME`` pointed at a 分身 (context-local override)."""
    from plobi_constants import reset_plobi_home_override, set_plobi_home_override

    def _run(profile_dir: Path, fn):
        token = set_plobi_home_override(str(profile_dir))
        try:
            _clear_skill_scanners()
            return fn()
        finally:
            reset_plobi_home_override(token)
            _clear_skill_scanners()

    return _run


def _clear_skill_scanners() -> None:
    """Drop every in-process skill/config cache between homes."""
    import agent.skill_utils as skill_utils
    import tools.skills_tool as skills_tool

    skill_utils._external_dirs_cache_clear()
    skills_tool._SKILLS_CACHE.clear()


def _registry_with_l2(home: Path, *, name: str = "demo") -> tuple[AgentRegistry, Path]:
    """Register one L1 secretary + one project L2 bound to a real temp folder."""
    project_dir = home.parent / "work" / name
    project_dir.mkdir(parents=True, exist_ok=True)
    reg = AgentRegistry.load()
    reg.upsert(
        AgentEntry(
            name="secretary",
            role="l1_secretary",
            provider="moonshot",
            model="kimi-k3",
        )
    )
    reg.upsert(
        AgentEntry(
            name=name,
            role="l2_project",
            profile=f"l2-{name}",
            category="projects",
            provider="deepseek",
            model="deepseek-chat",
            project_path=str(project_dir),
        )
    )
    reg.save()
    return reg, project_dir


def _visible_skill_names() -> dict[str, str]:
    """The skill list an agent in the active home would see: name -> description."""
    from tools.skills_tool import _find_all_skills

    return {s["name"]: s["description"] for s in _find_all_skills()}


# --------------------------------------------------------------------------- #
# 1. no copies, same visible set
# --------------------------------------------------------------------------- #


def test_spawn_lands_no_skill_copies_but_sees_the_shared_root(l2_home, in_profile):
    reg, _ = _registry_with_l2(l2_home)
    profile_dir = Path(reg.spawn("demo")["profile_dir"])

    own_skills = profile_dir / "skills"
    # No copied tree: not one SKILL.md under the 分身's own skills root, and no
    # bytes worth of a copy (the dir itself is bootstrapped empty by profiles).
    assert list(own_skills.rglob("SKILL.md")) == []
    assert sum(f.stat().st_size for f in own_skills.rglob("*") if f.is_file()) == 0

    # ``plobi update`` must not refill it (the marker is what suppresses seeding).
    assert (profile_dir / ".no-bundled-skills").is_file()

    # ... yet the very same skills stay discoverable through the shared root.
    from_top = in_profile(profile_dir, _visible_skill_names)
    from_default = _visible_skill_names()
    assert from_top, "分身 must still see a non-empty skill set"
    assert set(from_top) == set(from_default) == set(SHARED_SKILLS)

    # The system-prompt index (a second, independent scan surface) agrees.
    from agent.prompt_builder import build_skills_system_prompt

    index = in_profile(profile_dir, build_skills_system_prompt)
    for name in SHARED_SKILLS:
        assert name in index

    # 裁定 42 第二刀：config / .env / SOUL 这一层也不再是复印件（§42.2 只留三个
    # 按项目不同的覆盖：人设片段、模型、密钥）。
    soul = (profile_dir / "SOUL.md").read_text(encoding="utf-8")
    assert "PLOBI_L2_IDENTITY" in soul
    assert "shared persona" not in soul  # 顶层那份没有跟着搬进来
    assert "TEST_KEY=1" not in (profile_dir / ".env").read_text(encoding="utf-8")


def test_shared_root_is_written_into_the_profile_config(l2_home, in_profile):
    reg, _ = _registry_with_l2(l2_home)
    profile_dir = Path(reg.spawn("demo")["profile_dir"])

    cfg = yaml.safe_load((profile_dir / "config.yaml").read_text(encoding="utf-8"))
    dirs = cfg["skills"]["external_dirs"]
    assert [Path(d) for d in dirs] == [shared_skills_root()]

    # Every listed root must be one the scanner actually accepts when the 分身
    # is the active profile. (Read from the default home it would be dropped as
    # "same as my own local root" -- get_external_skills_dirs skips that.)
    from agent.skill_utils import get_external_skills_dirs

    resolved = in_profile(profile_dir, get_external_skills_dirs)
    assert resolved == [shared_skills_root().resolve()]


# --------------------------------------------------------------------------- #
# 2. idempotency
# --------------------------------------------------------------------------- #


def test_repeated_spawn_does_not_append_a_second_shared_root(l2_home, in_profile):
    reg, _ = _registry_with_l2(l2_home)
    profile_dir = Path(reg.spawn("demo")["profile_dir"])
    before = (profile_dir / "config.yaml").read_text(encoding="utf-8")

    assert reg.spawn("demo")["profile_dir"] == str(profile_dir)
    assert apply_shared_skills_root(profile_dir) is False
    assert apply_shared_skills_root(profile_dir) is False

    cfg = yaml.safe_load((profile_dir / "config.yaml").read_text(encoding="utf-8"))
    assert len(cfg["skills"]["external_dirs"]) == 1
    assert list((profile_dir / "skills").rglob("SKILL.md")) == []
    # Skills stay exactly as discoverable as before — no doubling either.
    assert set(in_profile(profile_dir, _visible_skill_names)) == set(SHARED_SKILLS)
    assert "external_dirs" in before  # the first spawn already wrote it

    # A pre-existing external dir owned by the user survives; we only add ours.
    cfg["skills"]["external_dirs"] = ["~/elsewhere"]
    (profile_dir / "config.yaml").write_text(
        yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8"
    )
    assert apply_shared_skills_root(profile_dir) is True
    after = yaml.safe_load((profile_dir / "config.yaml").read_text(encoding="utf-8"))
    assert after["skills"]["external_dirs"] == ["~/elsewhere", str(shared_skills_root())]


def test_apply_shared_skills_root_creates_minimal_config(tmp_path):
    """A 分身 without config.yaml still gets the binding (never fabricates one
    for a missing profile dir)."""
    profile_dir = tmp_path / "l2-fresh"
    assert apply_shared_skills_root(profile_dir) is True
    cfg = yaml.safe_load((profile_dir / "config.yaml").read_text(encoding="utf-8"))
    assert cfg["skills"]["external_dirs"] == [str(shared_skills_root())]
    assert apply_shared_skills_root(None) is False


# --------------------------------------------------------------------------- #
# 3. name collision semantics
# --------------------------------------------------------------------------- #


def test_local_skill_wins_over_shared_root_and_never_doubles(l2_home, in_profile):
    reg, _ = _registry_with_l2(l2_home)
    profile_dir = Path(reg.spawn("demo")["profile_dir"])

    collision = "alpha"  # a name the shared root also owns (fixture data)
    local = profile_dir / "skills" / "agent-authored"
    local.mkdir(parents=True, exist_ok=True)
    (local / "SKILL.md").write_text(
        f"---\nname: {collision}\ndescription: Locally authored override.\n---\n\nbody\n",
        encoding="utf-8",
    )

    def check():
        from tools.skills_tool import _find_all_skills

        entries = _find_all_skills()
        names = [s["name"] for s in entries]
        # deduped by name -> the list does not double ...
        assert len(names) == len(set(names))
        assert set(names) == set(SHARED_SKILLS)
        # ... and the 分身's own copy is the one that wins (skills_tool scans
        # the local root first, then external dirs — see _find_all_skills).
        winner = [s for s in entries if s["name"] == collision]
        assert len(winner) == 1
        assert winner[0]["description"] == "Locally authored override."

    in_profile(profile_dir, check)


# --------------------------------------------------------------------------- #
# 4. curator / usage attribution boundaries (no curator code changed here)
# --------------------------------------------------------------------------- #


def test_shared_skills_are_read_only_to_the_profile_curator(l2_home, in_profile):
    """The 分身 may discover shared skills but must never curate them."""
    reg, _ = _registry_with_l2(l2_home)
    profile_dir = Path(reg.spawn("demo")["profile_dir"])
    shared_md = shared_skills_root() / SHARED_SKILLS["alpha"][0] / "SKILL.md"

    def check():
        from tools import skill_usage

        # Enumerated as an external root -> not a curation candidate at all.
        assert skill_usage.is_external_skill_path(shared_md) is True
        assert skill_usage.list_agent_created_skill_names() == []
        assert skill_usage.is_curation_eligible("alpha") is False

        # Usage recorded inside the 分身 lands in its own sidecar, never in the
        # shared ledger — the attribution gap 裁定 42 §42.2 closes separately.
        shared_ledger = shared_skills_root() / ".usage.json"
        before = shared_ledger.read_text(encoding="utf-8") if shared_ledger.exists() else None
        skill_usage.bump_use("alpha")
        assert (profile_dir / "skills" / ".usage.json").is_file()
        after = shared_ledger.read_text(encoding="utf-8") if shared_ledger.exists() else None
        assert after == before
        assert skill_usage.load_usage()["alpha"]["use_count"] == 1

    in_profile(profile_dir, check)

    # The shared root itself is still fully curable by the default profile —
    # this slice only moved the 分身 off their own copies.
    from tools import skill_usage

    assert skill_usage.is_external_skill_path(shared_md) is False


# --------------------------------------------------------------------------- #
# 裁定 42 第二刀：config.yaml / .env 不再是复印件，密钥只以覆盖的形状存在
# --------------------------------------------------------------------------- #


def _md5(path: Path) -> str:
    import hashlib

    return hashlib.md5(path.read_bytes()).hexdigest()


def _assignment_lines(path: Path) -> list[str]:
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if "=" in line and not line.strip().startswith("#")
    ]


def test_spawn_lands_no_env_or_config_copy(l2_home):
    """新建的项目分身上不许有根 ``.env`` / 根 ``config.yaml`` 的副本。

    断的是内容，不是「文件在不在」：``create_profile`` 本来就会种一份只有注释
    表头的 ``.env``，那一句「有 .env」既测不出复印件也测不出覆盖丢了。
    """
    reg, _ = _registry_with_l2(l2_home)
    profile_dir = Path(reg.spawn("demo")["profile_dir"])

    profile_env = profile_dir / ".env"
    assert _md5(profile_env) != _md5(l2_home / ".env")
    assert _assignment_lines(profile_env) == []

    profile_cfg = profile_dir / "config.yaml"
    assert _md5(profile_cfg) != _md5(l2_home / "config.yaml")
    # 顶层那份 model 节没有跟进来（这一格要么是空的，要么是注册表声明的覆盖）。
    assert "test-model" not in profile_cfg.read_text(encoding="utf-8")


def test_secret_override_never_lands_on_disk(l2_home):
    """密钥覆盖读得到、注得进环境，但分身的家里一个字都不留。"""
    from dataclasses import replace as _replace

    from plobi.agents.registry import apply_secret_overrides

    reg, _ = _registry_with_l2(l2_home)
    reg.upsert(
        _replace(
            reg.get("demo"),
            secret_overrides={"GLM_API_KEY": "sk-project-only"},
        )
    )
    reg.save()

    profile_dir = Path(reg.spawn("demo")["profile_dir"])
    needle = b"sk-project-only"
    leftovers = [
        str(path)
        for path in profile_dir.rglob("*")
        if path.is_file() and needle in path.read_bytes()
    ]
    assert leftovers == []

    # 记录里带着它，运行时注进环境——两件事都成立才算「覆盖」而不是复印件。
    assert reg.get("demo").secret_overrides == {"GLM_API_KEY": "sk-project-only"}
    env: dict[str, str] = {}
    apply_secret_overrides(env, reg.get("demo"))
    assert env == {"GLM_API_KEY": "sk-project-only"}
