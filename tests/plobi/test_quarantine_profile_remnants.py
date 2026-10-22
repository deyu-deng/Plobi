"""Tests for ``scripts/plobi/quarantine_profile_remnants.py`` — 甲组隔离的三条要命的地方。

这把刀搬的是**真实用户目录里的东西**，而且 `profiles/` 不在任何 git 仓（搬坏了没有历史）。所以
测试钉的不是"搬了多少"，而是三件能让它变成事故的事：① dry-run 真的一个字节都不写（一次
自毁锚点的 dry-run 会让第二遍看不出自己已经搬过）；② 白名单挡住了 `state.db` 那一类真账
（10-22 现读 20 家各一份，那是会话总账，不是残留）；③ "空壳"这个前提每次现核，里面有文件
就当场拒绝而不是"照纸上的旧数搬"。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "plobi" / "quarantine_profile_remnants.py"


@pytest.fixture(scope="module")
def qr():
    spec = importlib.util.spec_from_file_location("quarantine_profile_remnants", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


DATE = "20261022"


def _home(qr, root: Path, name: str) -> Path:
    """造一个像样的分身家：甲组那六类 + 一堆**不许碰**的活东西。"""
    h = root / "profiles" / name
    h.mkdir(parents=True)
    for shell in qr.SHELL_CLASSES:
        (h / shell).mkdir(parents=True)
    (h / qr.FILE_CLASS).write_text("{}".encode("utf-8").decode("utf-8") * 8, encoding="utf-8")
    # 乙组 / 丙组 / 真账 —— 这些出现在树里也必须原地不动
    (h / "cron").mkdir()
    (h / "cron" / "jobs.json").write_text("[{\"id\": \"job-1\"}]", encoding="utf-8")
    (h / "sessions").mkdir()
    (h / "sessions" / "dump.json").write_text("{}", encoding="utf-8")
    (h / "logs").mkdir()
    (h / "logs" / "agent.log").write_text("line\n", encoding="utf-8")
    (h / "skills").mkdir()
    (h / "skills" / ".usage.json").write_text("{}", encoding="utf-8")
    (h / "SOUL.md").write_text("身份\n", encoding="utf-8")
    (h / ".env").write_text("SOME_KEY=secret\n", encoding="utf-8")
    (h / "state.db").write_bytes(b"SQLite format 3\x00")
    return h


@pytest.fixture
def root(qr, tmp_path: Path) -> Path:
    """两个家：一个满壳，一个连壳都没有（用来证明"份数"这个分母不会被动歪）。"""
    _home(qr, tmp_path, "aura")
    (tmp_path / "profiles" / "kit").mkdir(parents=True)
    (tmp_path / "models_dev_cache.json").write_text("{}", encoding="utf-8")  # 顶层那一份真源
    return tmp_path / "profiles"


def _run(qr, root: Path, apply: bool, capsys) -> dict:
    argv = ["--date", DATE] + (["--apply"] if apply else []) + ["--profiles-root", str(root)]
    rc = qr.main(argv)
    out = capsys.readouterr().out
    return {"rc": rc, "report": json.loads(out)}


def test_dry_run_writes_not_a_single_byte(qr, root, capsys):
    res = _run(qr, root, apply=False, capsys=capsys)
    rep = res["report"]
    assert rep["mode"] == "dry-run"
    assert res["rc"] == 0
    assert rep["planned"]["moves"] == len(qr.ALLOWED)  # 六类，全在 aura 那一家里
    # 判据本体：计划算出来了，盘上什么都没变
    assert list(root.glob("*/_quarantine-*")) == []
    assert (root / "aura" / qr.FILE_CLASS).is_file()
    for shell in qr.SHELL_CLASSES:
        assert (root / "aura" / shell).is_dir()
    assert rep["before"]["classes"] == rep["after"]["classes"]


def test_apply_moves_the_allowlisted_and_keeps_the_house_count(qr, root, capsys):
    before_homes = _run(qr, root, apply=False, capsys=capsys)["report"]["before"]["homes"]
    res = _run(qr, root, apply=True, capsys=capsys)
    rep = res["report"]

    assert res["rc"] == 0, rep
    assert rep["errors"] == []
    assert rep["after"]["homes"] == before_homes == 2  # 判据：份数不变
    assert rep["after"]["classes"][qr.FILE_CLASS]["homes"] == 0
    for shell in qr.SHELL_CLASSES:
        assert rep["after"]["classes"][shell]["homes"] == 0
    q = root / "aura" / f"_quarantine-{DATE}"
    assert (q / qr.FILE_CLASS).is_file()
    for shell in qr.SHELL_CLASSES:
        assert (q / shell).is_dir()
    # 只移不删 ⇒ 字节还在盘上，只是从"分身家的东西"变成"隔离区里的东西"。
    # 谁要是哪天让这个脚本真删了，这一条会当场红——那是用户当场签之后才许做的事。
    assert rep["after"]["total_bytes"] == rep["before"]["total_bytes"]
    assert (q / qr.FILE_CLASS).read_text(encoding="utf-8") == "{}" * 8


def test_real_accounts_never_move(qr, root, capsys):
    """`state.db` / `cron/jobs.json` / `SOUL.md` / `.env` / `logs` / `skills` / `sessions`
    是乙丙组和真账——它们躺在同一个目录里，白名单必须一次都不碰。"""
    kept = [
        ("aura", "state.db"), ("aura", "SOUL.md"), ("aura", ".env"),
        ("aura", "cron", "jobs.json"), ("aura", "sessions", "dump.json"),
        ("aura", "logs", "agent.log"), ("aura", "skills", ".usage.json"),
    ]
    _run(qr, root, apply=True, capsys=capsys)
    for parts in kept:
        assert root.joinpath(*parts).exists(), parts
    # 隔离区里只许出现甲组那六类，一个名字都不许多
    q = root / "aura" / f"_quarantine-{DATE}"
    assert sorted(p.name for p in q.iterdir()) == sorted(qr.ALLOWED)


def test_nonempty_shell_is_refused_and_left_whole(qr, root, capsys):
    """纸上的"空壳"是 10-20 的读数。今天里面要有东西了就必须拒绝，不许照旧数搬。"""
    (root / "aura" / "plans" / "note.md").write_text("别搬我\n", encoding="utf-8")
    res = _run(qr, root, apply=True, capsys=capsys)
    rep = res["report"]

    refused = {r["class"] for r in rep["planned"]["refused"]}
    assert refused == {"plans"}
    assert (root / "aura" / "plans" / "note.md").is_file()  # 原地不动
    # 同一家其余五类照搬——拒绝一档不该把整批带走
    assert not (root / "aura" / "skins").exists()
    assert (root / "aura" / f"_quarantine-{DATE}" / "skins").is_dir()
    # 而且这一家现在还剩一档甲组没搬走 ⇒ 判据要出声（rc 非 0），不能报成"搬干净了"
    assert res["rc"] == 1


def test_rerun_is_idempotent(qr, root, capsys):
    """重跑第二遍必须"什么都发现不了"，而且不许把隔离区里那份旧的覆盖掉。

    后半段是这台的真现场：`ensure_plobi_home()` 每轮 `load_config` 都跑，所以搬过的壳
    **会被重新建出来**。那时目标位已经有旧副本，正确动作是 skip 并报出来，不是 move 覆盖。
    """
    _run(qr, root, apply=True, capsys=capsys)
    q = root / "aura" / f"_quarantine-{DATE}"
    first_copy = sorted(p.name for p in q.iterdir())

    second = _run(qr, root, apply=True, capsys=capsys)["report"]
    assert second["planned"]["moves"] == 0
    assert second["moved"] == []
    assert second["errors"] == []
    assert sorted(p.name for p in q.iterdir()) == first_copy

    (root / "aura" / "plans").mkdir()  # 播种机把壳建回来了
    third = _run(qr, root, apply=True, capsys=capsys)["report"]
    assert third["planned"]["moves"] == 0
    assert [s["class"] for s in third["planned"]["already_quarantined"]] == ["plans"]
    assert (root / "aura" / "plans").is_dir(), "skip 不是删除——新建那个空壳不许被顺手清掉"


def test_read_chain_still_points_at_the_shared_copy(qr, root, capsys):
    """搬完之后每家仍必须解析到顶层那一份（`shared_root_for`）。
    这一格是"零行为变化"的正面证据——只有"没报错"不算证明。"""
    homes = qr.homes_of(root)
    chain_before = qr.read_chain(homes)
    assert chain_before["fell_back_into_home"] == 0
    assert chain_before["shared_copy_missing"] == 0
    assert chain_before["read_shared_copy"] == 2

    _run(qr, root, apply=True, capsys=capsys)
    chain_after = qr.read_chain(qr.homes_of(root))
    assert chain_after == chain_before


def test_missing_profiles_root_is_not_a_crash(qr, tmp_path, capsys):
    """新装机或 `--profiles-root` 打错：应当报 0 家、退 0，而不是抛栈。
    （打错路径静默 0 命中是另一回事，所以 root_resolved_by 要印出来。）"""
    rep = _run(qr, tmp_path / "nope", apply=True, capsys=capsys)["report"]
    assert rep["before"]["homes"] == 0
    assert rep["planned"]["moves"] == 0
    assert rep["root_resolved_by"] == "--profiles-root"
