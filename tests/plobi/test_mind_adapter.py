"""WP-MIND 窄切适配：MindProvider 读/写接缝 + mind_sync Surface 子树通道。

对照 MIND_ADAPTER_PLAN.md（Docs/）与任务书验收：
1. 读接缝——prefetch 召回带画像/项目状态摘要（日志可证，失败降级）；
2. 写接缝——每日日程摘要经 mind_sync → Mind/wiki/daily/（脑里；surface 不进记忆系统），
   无模型、串行 MindWriter、幂等；写失败只告警不阻塞主对话流。
3. 边界——运行时状态真源仍是 SQLite（ADR-0007）；非 primary 上下文不写。
"""

from __future__ import annotations

import importlib.util
from datetime import datetime
import logging
from pathlib import Path

import pytest

@pytest.fixture(autouse=True)
def _l1_gate_off_here(monkeypatch):
    """这一档测的是「写/提交」那条管线本身，不是 L1 那道闸。

    闸有自己的测试档（``tests/plobi/test_mind_l1_gate.py``）；这里按「显式要求时才拦」跑，
    否则每个用例都会被正确地问一次「要不要点头」，把管线测试变成待确认层测试。
    """
    import plobi.mind.writer as writer_mod

    monkeypatch.setattr(
        writer_mod.MindWriter, "_gate_refusals",
        lambda self, batch: ["显式要求先点头"] if self.require_approval is True else [],
    )


from plobi.agenda import store


def _load_provider_module():
    """provider 已在内核（裁定 198）：直接按包导入，不再按文件路径加载插件目录。"""
    import plobi.mind.provider as module

    return module


MIND_MOD = _load_provider_module()


@pytest.fixture()
def surface(tmp_path, monkeypatch):
    """临时 Mind surface：persona + 大写 Plobi 项目（当前写入位）。"""
    root = tmp_path / "Mind"
    (root / "persona").mkdir(parents=True)
    (root / "Surface" / "dossiers" / "projects" / "Plobi").mkdir(parents=True)
    (root / "AGENTS.md").write_text("# Mind", encoding="utf-8")
    # 详版画像在脑里（用户 10-24：surface 不进记忆系统；USER.md 是精简版）
    (root / "persona" / "profile.md").write_text(
        "我是小龟，浙大本科生。", encoding="utf-8"
    )
    (root / "Surface" / "dossiers" / "projects" / "Plobi" / "plan.md").write_text(
        "# Plobi\n北极星：人只负责审核和提想法。", encoding="utf-8"
    )
    (root / "Surface" / "dossiers" / "projects" / "Plobi" / "progress.md").write_text(
        "M1 推进中，Wave B 完成。", encoding="utf-8"
    )
    # mind_sync/MindWriter 走 plobi.mind.paths.resolve_root（env 优先）——
    # 必须指到临时 surface，否则会解析到真实仓库的 mind/ 并写进去。
    monkeypatch.setenv("MIND_ROOT", str(root))
    monkeypatch.setattr(MIND_MOD, "_resolve_root", lambda: root)
    return root


@pytest.fixture()
def provider():
    return MIND_MOD.MindProvider()


# ── 接缝 1：读——召回带画像摘要 ───────────────────────────────────────────────


def test_prefetch_includes_profile_summary(surface, provider, caplog):
    with caplog.at_level(logging.INFO, logger=MIND_MOD.__name__):
        result = provider.prefetch("随便问点什么")
    assert "[Mind 画像]" in result
    assert "我是小龟" in result
    assert "### 项目 Plobi" in result
    assert "M1 推进中" in result
    # 验收 2：画像读取路径生效，日志可证
    assert any("prefetch includes profile summary" in r.message for r in caplog.records)


def test_prefetch_profile_read_failure_is_non_fatal(surface, provider, caplog, monkeypatch):
    def boom(root):
        raise RuntimeError("disk exploded")

    monkeypatch.setattr(MIND_MOD, "_profile_block", boom)
    with caplog.at_level(logging.WARNING, logger=MIND_MOD.__name__):
        result = provider.prefetch("查询")
    assert "Mind 画像" not in result  # 降级为无画像，不抛异常
    assert any("profile summary read failed" in r.message for r in caplog.records)


def test_prefetch_without_surface_returns_empty(provider, monkeypatch):
    monkeypatch.setattr(
        MIND_MOD, "_resolve_root", lambda: Path("mind-root-not-configured")
    )
    assert provider.prefetch("查询") == ""


def test_profile_block_is_bounded(tmp_path, monkeypatch):
    root = tmp_path / "Mind"
    (root / "Surface" / "meta").mkdir(parents=True)
    (root / "Surface" / "dossiers" / "projects" / "Plobi").mkdir(parents=True)
    (root / "AGENTS.md").write_text("# Mind", encoding="utf-8")
    (root / "Surface" / "meta" / "Persona.md").write_text("p" * 5000, encoding="utf-8")
    (root / "Surface" / "dossiers" / "projects" / "Plobi" / "plan.md").write_text("q" * 5000, encoding="utf-8")
    block = MIND_MOD._profile_block(root)
    assert len(block) < 3000  # 每段 800 截断，窄切不淹上下文
    assert "…" in block


# ── 接缝 2：写——每日日程摘要 → Mind/wiki/daily/（脑里；surface 不进记忆系统）─────────


@pytest.fixture()
def agenda_env(tmp_path, monkeypatch):
    """agenda.db 隔离 + 一条今天的 confirmed 事件（真源在 SQLite）。"""
    db = tmp_path / "agenda.db"
    monkeypatch.setenv("PLOBI_AGENDA_DB", str(db))
    today = datetime.now().date().isoformat()
    conn = store.connect(db)
    try:
        store.create_event(
            conn,
            title="组会汇报",
            start_at=f"{today}T10:00:00",
            end_at=f"{today}T11:00:00",
            kind="task",
            status="confirmed",
        )
        conn.commit()
    finally:
        conn.close()
    return db


def test_sync_turn_publishes_daily_agenda_once(surface, agenda_env, provider):
    provider.initialize("sess-1")

    provider.sync_turn("今天有什么安排？", "今天 10 点有组会汇报。")

    daily = surface / "wiki" / "daily"
    files = list(daily.glob("*.md"))
    assert len(files) == 1  # 当日一个文件
    content = files[0].read_text(encoding="utf-8")
    assert "组会汇报" in content  # 内容与 agenda.db 一致（daily_summary 渲染）

    # 同一天第二轮流不再重写（幂等，不制造 git 噪音）
    mtime = files[0].stat().st_mtime_ns
    provider.sync_turn("第二轮流", "好的。")
    assert files[0].stat().st_mtime_ns == mtime


def test_sync_turn_skips_non_primary_context(surface, agenda_env):
    provider = MIND_MOD.MindProvider()
    provider.initialize("sess-cron", agent_context="cron")
    provider.sync_turn("x", "y")
    assert not (surface / "wiki" / "daily").exists()


def test_sync_turn_write_failure_never_blocks_turn(
    surface, agenda_env, provider, caplog, monkeypatch
):
    """验收 3（故障注入）：写路径炸掉 → 主对话不受阻，仅告警。"""
    provider.initialize("sess-1")

    import plobi.agenda.mind_sync as mind_sync_mod

    def boom(*args, **kwargs):
        raise RuntimeError("mind surface locked")

    monkeypatch.setattr(mind_sync_mod, "publish_project_daily_summary", boom)
    with caplog.at_level(logging.WARNING, logger=MIND_MOD.__name__):
        provider.sync_turn("你好", "在的。")  # 不抛异常即通过
    assert any("daily agenda summary failed" in r.message for r in caplog.records)


def test_sync_turn_writer_soft_failure_is_warning_only(
    surface, agenda_env, provider, caplog, monkeypatch
):
    """写服务返回 ok=False（surface 不可用等）→ 同样只告警。"""
    provider.initialize("sess-1")

    import plobi.agenda.mind_sync as mind_sync_mod
    from plobi.mind.writer import WriteResult

    monkeypatch.setattr(
        mind_sync_mod,
        "publish_project_daily_summary",
        lambda **kw: WriteResult(ok=False, written=[], skipped=["x"], detail="mind unavailable"),
    )
    with caplog.at_level(logging.WARNING, logger=MIND_MOD.__name__):
        provider.sync_turn("你好", "在的。")
    assert any("not written" in r.message for r in caplog.records)


def test_sync_turn_publishes_agenda_and_writes_no_chat_material(
    surface, agenda_env, provider, monkeypatch
):
    """裁定 198：``sync_turn`` 不再往脑里落第二份聊天原文，每日摘要那条腿照旧要落。

    钉两件事：① 这一趟**没有任何一次写**的路径里带 ``raw``（原料区作废，写者归零）；
    ② 日程摘要照旧发布（会话正本是单库 ``state.db``，不是脑里那份拷贝）。
    """
    provider.initialize("sess-1")

    import plobi.mind.writer as writer_mod

    written: list[str] = []
    real_write_one = writer_mod.MindWriter.write_one

    def spy_write_one(self, rel, content, *, mode="overwrite"):
        written.append(rel)
        return real_write_one(self, rel, content, mode=mode)

    monkeypatch.setattr(writer_mod.MindWriter, "write_one", spy_write_one)
    provider.sync_turn("你好", "在的。")

    assert not [rel for rel in written if "raw" in rel.split("/")], written
    assert (surface / "wiki" / "daily").is_dir()


def test_publish_project_daily_summary_is_safe_and_idempotent(surface, agenda_env, tmp_path):
    from plobi.agenda.mind_sync import (
        project_daily_relative_path,
        publish_project_daily_summary,
    )
    from plobi.mind.writer import MindWriter

    rel = project_daily_relative_path()
    assert rel.startswith("Mind/wiki/daily/")
    writer = MindWriter(surface, run_verifier=False, commit=False)
    first = publish_project_daily_summary(writer=writer)
    assert first.ok, first.detail
    second = publish_project_daily_summary(writer=writer)
    assert second.ok
    files = list((surface / "wiki" / "daily").glob("*.md"))
    assert len(files) == 1  # 覆盖而非累积


def test_publish_project_daily_summary_without_surface_is_soft(tmp_path, monkeypatch):
    monkeypatch.setenv("MIND_ROOT", str(tmp_path / "no-such-surface"))
    from plobi.agenda.mind_sync import publish_project_daily_summary

    result = publish_project_daily_summary()
    assert result.ok is False
    assert result.detail == "mind unavailable"
