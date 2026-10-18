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


def test_shared_root_is_written_into_the_one_shared_config(l2_home, in_profile):
    """技能那根线只绑一次：写进**顶层那一份** ``config.yaml``，分身家不再各存一份。

    裁 42.3 第 2 条原来落成的形状是「每个分身家自己的 ``config.yaml`` 里写一条
    ``skills.external_dirs``」——现核这台 24 份、那一条 24 份**逐字相同**，所以它从来
    不是"这个分身特有的东西"，只是复印件。裁 166 把配置收成一份之后就写那一份。

    这条同时是「停读没有把技能读瞎」的判别读数：分身家已经没有那份文件了，
    ``get_external_skills_dirs`` 走解析器照样解析出同一个共享根。（顶层家自己读那一条
    会被跳过——它等于自己的本地主根，这正好说明同一个值在两边都不出错。）
    """
    reg, _ = _registry_with_l2(l2_home)
    profile_dir = Path(reg.spawn("demo")["profile_dir"])

    top = yaml.safe_load((l2_home / "config.yaml").read_text(encoding="utf-8"))
    assert [Path(d) for d in top["skills"]["external_dirs"]] == [shared_skills_root()]
    assert not (profile_dir / "config.yaml").exists()

    from agent.skill_utils import get_external_skills_dirs

    resolved = in_profile(profile_dir, get_external_skills_dirs)
    assert resolved == [shared_skills_root().resolve()]


# --------------------------------------------------------------------------- #
# 2. idempotency
# --------------------------------------------------------------------------- #


def test_repeated_spawn_does_not_append_a_second_shared_root(l2_home, in_profile):
    """幂等落在顶层那一份上：重复 spawn 不追加第二条，用户自己列的外部根留着。"""
    reg, _ = _registry_with_l2(l2_home)
    profile_dir = Path(reg.spawn("demo")["profile_dir"])
    top_cfg = l2_home / "config.yaml"
    before = top_cfg.read_text(encoding="utf-8")

    assert reg.spawn("demo")["profile_dir"] == str(profile_dir)
    assert apply_shared_skills_root() is False
    assert apply_shared_skills_root() is False

    cfg = yaml.safe_load(top_cfg.read_text(encoding="utf-8"))
    assert len(cfg["skills"]["external_dirs"]) == 1
    assert list((profile_dir / "skills").rglob("SKILL.md")) == []
    # Skills stay exactly as discoverable as before — no doubling either.
    assert set(in_profile(profile_dir, _visible_skill_names)) == set(SHARED_SKILLS)
    assert "external_dirs" in before  # the first spawn already wrote it

    # A pre-existing external dir owned by the user survives; we only add ours.
    cfg["skills"]["external_dirs"] = ["~/elsewhere"]
    top_cfg.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    assert apply_shared_skills_root() is True
    after = yaml.safe_load(top_cfg.read_text(encoding="utf-8"))
    assert after["skills"]["external_dirs"] == ["~/elsewhere", str(shared_skills_root())]


def test_apply_shared_skills_root_creates_the_shared_config_when_absent(tmp_path, monkeypatch):
    """顶层那份 ``config.yaml`` 还没落地（新装机）时，这一句把它建出来。

    以前这一条断的是「分身没有 config.yaml 也要给它建一份最小的」——那份文件如今
    不该存在，所以建的对象换成那唯一一份。
    """
    root = tmp_path / "fresh-root"
    (root / "plobi").mkdir(parents=True)
    monkeypatch.setenv("PLOBI_HOME", str(root))
    target = root / "config.yaml"
    assert not target.exists()

    assert apply_shared_skills_root() is True

    cfg = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert cfg["skills"]["external_dirs"] == [str(shared_skills_root())]


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

    # ``config.yaml`` 那一腿整个不再落进分身家（裁 166 / ADR-0013：配置只有一份，
    # 分身家 0 份可读；连"只有注册表声明的覆盖"那一格也改成读时叠，见
    # ``plobi.agents.registry.profile_config_overlay``）。
    assert not (profile_dir / "config.yaml").exists()


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
    from plobi_cli.config import INJECTED_SECRETS_ENV

    assert env["GLM_API_KEY"] == "sk-project-only"
    # 注入要登记名字，否则分身体里那份旧 .env 会压过它（见 WP-AGENT-SECRET-SOURCE）。
    # 登记的是名字集合，共享家目录里还有哪些凭据就一并登记，所以只判这一条在里面。
    registered = env[INJECTED_SECRETS_ENV].split(",")
    assert "GLM_API_KEY" in registered
    assert "sk-project-only" not in registered
