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
import json
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
    """临时脑 + 假人读面：画像在脑里，档案在人读面的 ``dossiers/``。

    11-09／11-10 两刀之后布局变了，夹具必须跟着走，否则测的是一个**已经不存在的
    形状**（旧夹具把 ``profile.md`` 与 ``plan.md`` 都塞在 ``<脑>/Surface`` 下，
    产品永远读不到，却因为断的也是那个幽灵路径而全绿）：

    * 脑**扁平**：``<脑>/persona/profile.md``、``<脑>/wiki/…``（不再有 ``Mind/`` 那一层）；
    * 人读面按种类分家：``<人读面>/dossiers/projects/<名>/dossier.json``（档案本体，
      ``plan.md``／``progress.md`` 已退休）；
    * 两根指针都钉到临时库：脑仓门禁只认旧名 ``SURFACE_ROOT``（没有就按「脑的兄弟
      ``vault/``」找），产品认 ``HUMAN_ROOT`` 优先——只钉一个就会两边各走各的。
    """
    root = tmp_path / "mind"
    (root / "persona").mkdir(parents=True)
    (root / "AGENTS.md").write_text("# Mind", encoding="utf-8")
    # 详版画像在脑里（用户 10-24：人读面不进记忆系统；USER.md 是精简版）
    (root / "persona" / "profile.md").write_text(
        "我是小龟，浙大本科生。", encoding="utf-8"
    )

    human = tmp_path / "vault"
    project = human / "dossiers" / "projects" / "Plobi"
    project.mkdir(parents=True)
    (project / "dossier.json").write_text(
        json.dumps(
            {
                "version": 1,
                "agent": "Plobi",
                "title": "Plobi",
                "category": "projects",
                "goal": "北极星：人只负责审核和提想法。",
                "status": "M1 推进中，Wave B 完成。",
                "stages": [
                    {"id": "s1", "title": "Wave B", "detail": "", "done": True},
                ],
                "registry": {"project": "Plobi", "state": "active"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("HUMAN_ROOT", str(human))
    monkeypatch.setenv("SURFACE_ROOT", str(human))
    # mind_sync/MindWriter 走 plobi.mind.paths.resolve_root（env 优先）——
    # 必须指到临时脑，否则会解析到真实仓库的 mind/ 并写进去。
    monkeypatch.setenv("MIND_ROOT", str(root))
    monkeypatch.setattr(MIND_MOD, "_resolve_root", lambda: root)
    return root


@pytest.fixture()
def provider():
    return MIND_MOD.MindProvider()


# ── 接缝 1：读——召回带画像摘要 ───────────────────────────────────────────────


def test_prefetch_includes_project_briefs_and_defers_persona(surface, provider, caplog):
    """prefetch 带**档案**进上下文，画像交给 ``USER PROFILE`` 段（审计 P4 防双份）。

    11-09 那一刀之后 ``prefetch`` 走 ``include_persona=False``：画像正本已经由稳定
    记忆段整段给进系统提示，这里再抄一遍就是同一段话出现两次。所以这里断的是
    「项目档案进来了」＋「画像正文没重复进来」。
    """
    with caplog.at_level(logging.INFO, logger=MIND_MOD.__name__):
        result = provider.prefetch("随便问点什么")
    assert "[Mind 画像]" in result
    assert "### 项目 Plobi" in result
    assert "北极星：人只负责审核和提想法。" in result
    assert "M1 推进中" in result
    assert "我是小龟" not in result  # 画像由 USER PROFILE 段负责，这里不重复
    # 验收 2：档案读取路径生效，日志可证
    assert any("prefetch includes project briefs" in r.message for r in caplog.records)


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
    """两段都截断（画像 800 字、档案 excerpt 有上限）——窄切不许淹上下文。

    夹具照现核造：画像在脑里 ``persona/profile.md``，档案是人读面的 ``dossier.json``。
    旧版往 ``<脑>/Surface/meta/Persona.md`` 写，产品早就改成读不到那个位置，于是这条
    用例断言的是一个空字符串——正好挡住「读错路径也绿」的那类假绿。
    """
    root = tmp_path / "mind"
    (root / "persona").mkdir(parents=True)
    (root / "AGENTS.md").write_text("# Mind", encoding="utf-8")
    (root / "persona" / "profile.md").write_text("p" * 5000, encoding="utf-8")

    human = tmp_path / "vault"
    project = human / "dossiers" / "projects" / "Plobi"
    project.mkdir(parents=True)
    (project / "dossier.json").write_text(
        json.dumps(
            {
                "version": 1,
                "agent": "Plobi",
                "title": "Plobi",
                "category": "projects",
                "goal": "q" * 5000,
                "status": "",
                "stages": [],
                "registry": {"project": "Plobi"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HUMAN_ROOT", str(human))
    monkeypatch.setenv("SURFACE_ROOT", str(human))

    block = MIND_MOD._profile_block(root)
    assert block  # 两段都真读到了东西，不是空串
    assert len(block) < 3000  # 每段截断，窄切不淹上下文
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


def test_sync_turn_no_longer_publishes_the_daily_agenda(surface, agenda_env, provider):
    """10-29 用户把「每轮排一张每日摘要」那条自动腿裁了，别悄悄接回来。

    原话：「每日摘要是干什么用的。我都没有定义过。」——那份计划没人点头，却每开一个
    新进程／新一天就再排一张，待确认层堆到 22 张。于是 ``sync_turn`` 成了**有意的
    空操作**；真要出那一份得由人显式叫 ``publish_project_daily_summary``。
    """
    provider.initialize("sess-1")

    provider.sync_turn("今天有什么安排？", "今天 10 点有组会汇报。")

    assert not (surface / "wiki" / "daily").exists()


def test_sync_turn_skips_non_primary_context(surface, agenda_env):
    provider = MIND_MOD.MindProvider()
    provider.initialize("sess-cron", agent_context="cron")
    provider.sync_turn("x", "y")
    assert not (surface / "wiki" / "daily").exists()


class _RaisingWriter:
    """写盘必炸的写者（鸭子类型：``publish_*`` 只用到 ``available`` 与 ``write_one``）。"""

    available = True

    def write_one(self, *args, **kwargs):
        raise RuntimeError("mind surface locked")


class _RefusingWriter:
    """写服务软失败（``ok=False``，例如锁被占／白名单外）。"""

    available = True

    def write_one(self, *args, **kwargs):
        from plobi.mind.writer import WriteResult

        return WriteResult(
            ok=False, written=[], skipped=["x"], detail="mind unavailable"
        )


def test_explicit_daily_summary_never_raises_on_write_failure(
    surface, agenda_env, caplog
):
    """人显式叫它时写盘炸掉 → 返回 ``ok=False`` 并告警，绝不向上抛（WP-L1-BUDGET §3）。

    这条腿现在只有人显式叫才跑，但「不许把主回合拖下水」那条红线不变，所以故障注入
    从 ``sync_turn`` 挪到这里再钉一遍。
    """
    import plobi.agenda.mind_sync as mind_sync_mod

    with caplog.at_level(logging.WARNING, logger=mind_sync_mod.__name__):
        result = mind_sync_mod.publish_project_daily_summary(writer=_RaisingWriter())

    assert result.ok is False
    assert any("skipped" in r.message for r in caplog.records)


def test_explicit_daily_summary_soft_failure_is_warning_only(surface, agenda_env, caplog):
    """写服务返回 ``ok=False``（人读面不可用等）→ 同样只告警。"""
    import plobi.agenda.mind_sync as mind_sync_mod

    with caplog.at_level(logging.WARNING, logger=mind_sync_mod.__name__):
        result = mind_sync_mod.publish_project_daily_summary(writer=_RefusingWriter())

    assert result.ok is False
    assert any("not written" in r.message for r in caplog.records)


def test_sync_turn_writes_nothing_at_all(surface, agenda_env, provider, monkeypatch):
    """裁定 198 ＋ 10-29：这一趟既不落聊天原文，也不再排每日摘要——**一次写都没有**。

    会话正本是单库 ``state.db``（裁定 180 行级归属），脑里不该收第二份拷贝；每日摘要
    那条自动腿由人显式叫才跑。两条都关掉之后，``sync_turn`` 应当连一次 ``write_one``
    都不发生——这条断言就是那个「零」的守门人。
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

    assert written == []


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
