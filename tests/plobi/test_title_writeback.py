"""改名写回（11-22）：能写回的家真写进它自己那一格，写不了的家照实说。

全部用 tmp 里的假家目录，不碰这台机器上真软件的数据。

四条底线各有一条测试盯着：
1. 写成了要能读回来（``written`` + ``verified``）；
2. 它自己没起过名就不许硬塞一条假记录（``no_slot``）；
3. 软件开着就不许动（``busy``，账本也不改）；
4. 写不了的家不许装作写成了（``ledger_only`` + 一句人话）。
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from plobi.delegation import external_sessions as es  # noqa: E402
from plobi.delegation import title_writeback as twb  # noqa: E402


@pytest.fixture
def homes(tmp_path, monkeypatch):
    """三家的假家目录：Marvis（sqlite）／CodeBuddy（jsonl）／Kiro（sidecar）。"""
    home = tmp_path / "home"
    marvis = tmp_path / "marvis" / "database"
    codebuddy = tmp_path / "codebuddy"
    kiro = tmp_path / "kiro"
    for directory in (home, marvis, codebuddy / "projects", kiro):
        directory.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PLOBI_HOME", str(home))
    monkeypatch.setenv("MARVIS_HOME", str(marvis.parent))
    monkeypatch.setenv("CODEBUDDY_CONFIG_DIR", str(codebuddy))
    monkeypatch.setattr(es, "marvis_db", lambda: marvis / "data.db")
    monkeypatch.setattr(es, "codebuddy_root", lambda: codebuddy / "projects")
    monkeypatch.setattr(es, "kiro_root", lambda: kiro)
    # 各家进程探测一律当「没开着」，单条测试要拦的时候自己再盖
    monkeypatch.setattr(twb, "_running", lambda _vendor: "")
    monkeypatch.delenv(twb.DISABLE_ENV, raising=False)
    es._ALL_CACHE.update({"at": 0.0, "rows": []})
    es._META_CACHE.clear()
    return SimpleNamespace(home=home, marvis=marvis, codebuddy=codebuddy, kiro=kiro)


def _marvis_db(homes: SimpleNamespace, rows: list[tuple[str, str]]) -> Path:
    db = homes.marvis / "data.db"
    con = sqlite3.connect(str(db))
    try:
        con.execute("CREATE TABLE conversations (conversation_id TEXT, title TEXT)")
        con.executemany("INSERT INTO conversations VALUES (?, ?)", rows)
        con.commit()
    finally:
        con.close()
    return db


def _title_in(db: Path, session_id: str) -> str:
    con = sqlite3.connect(str(db))
    try:
        row = con.execute(
            "SELECT title FROM conversations WHERE conversation_id = ?", (session_id,)
        ).fetchone()
    finally:
        con.close()
    return str(row[0]) if row else ""


def test_marvis_title_lands_in_its_own_table(homes):
    """Marvis 那种：它自己的会话名就在表里 ⇒ 改完它自己也能看见。"""
    db = _marvis_db(homes, [("conv-1", "旧名字")])

    result = twb.writeback_title("marvis", "conv-1", "新名字")

    assert result["status"] == "written"
    assert result["ok"] is True and result["verified"] is True
    assert _title_in(db, "conv-1") == "新名字"
    assert Path(result["backup"]).exists()


def test_codebuddy_ai_title_record_is_rewritten_without_touching_the_chat(homes):
    """CodeBuddy 那种：只改 ``ai-title`` 那条记录的标题格，聊天内容一个字不动。"""
    project = homes.codebuddy / "projects" / "demo"
    project.mkdir(parents=True, exist_ok=True)
    path = project / "sess-1.jsonl"
    path.write_text(
        "\n".join(
            json.dumps(row, ensure_ascii=False)
            for row in (
                {"type": "user", "message": {"content": "帮我看看这个"}},
                {"type": "ai-title", "aiTitle": "旧标题"},
                {"type": "assistant", "message": {"content": "好"}},
            )
        )
        + "\n",
        encoding="utf-8",
    )

    result = twb.writeback_title("codebuddy", "sess-1", "新的名字")

    assert result["status"] == "written" and result["verified"] is True
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert records[1]["aiTitle"] == "新的名字"
    assert records[0]["message"]["content"] == "帮我看看这个"


def test_codebuddy_without_a_title_record_does_not_fake_one(homes):
    """它自己还没起过名 ⇒ 不许硬塞一条 ``ai-title`` 假记录充数。"""
    project = homes.codebuddy / "projects" / "demo"
    project.mkdir(parents=True, exist_ok=True)
    path = project / "sess-2.jsonl"
    path.write_text(
        "\n".join(
            json.dumps(row, ensure_ascii=False)
            for row in (
                {"type": "user", "message": {"content": "就一句话"}},
                {"type": "assistant", "message": {"content": "嗯"}},
            )
        )
        + "\n",
        encoding="utf-8",
    )

    result = twb.writeback_title("codebuddy", "sess-2", "想改的名字")

    assert result["status"] == "no_slot"
    assert result["ok"] is False
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2
    assert "只在 Plobi 里生效" in twb.status_line(result, "codebuddy")


def test_kiro_sidecar_title_is_rewritten_and_other_keys_survive(homes):
    """Kiro 那种：标题只活在旁挂文件里 ⇒ 改 ``title``，别人的键一个不能丢。"""
    path = homes.kiro / "sess-3.json"
    path.write_text(
        json.dumps({"title": "", "cwd": "D:/Projects/x", "updated_at": 1}, ensure_ascii=False),
        encoding="utf-8",
    )

    result = twb.writeback_title("kiro", "sess-3", "会话新名")

    assert result["status"] == "written" and result["verified"] is True
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["title"] == "会话新名"
    assert payload["cwd"] == "D:/Projects/x"
    assert payload["updated_at"] == 1


def test_qoder_is_ledger_only_and_says_so_in_plain_words(homes):
    """B/C 档：标题就是它的聊天内容（或它自己进程里的索引）⇒ 不写，但要说明白。"""
    result = twb.writeback_title("qoder", "sess-4", "随便改改")

    assert result["status"] == "ledger_only"
    assert result["ok"] is True
    assert twb.writable("qoder") is False
    assert "只在 Plobi 里生效" in twb.status_line(result, "qoder")


def test_running_software_blocks_the_write(homes, monkeypatch):
    """软件开着时改它，它会拿内存里的旧名字盖回去 ⇒ 先拦下。"""
    monkeypatch.setattr(twb, "_running", lambda _vendor: "CodeBuddy.exe")

    result = twb.writeback_title("codebuddy", "sess-5", "新名字")

    assert result["status"] == "busy"
    assert result["ok"] is False
    assert "先把它关掉" in result["note"]


def test_readback_mismatch_counts_as_not_written(homes, monkeypatch):
    """写进库了但读回来对不上 ⇒ 当作没写成，别报成功。"""
    _marvis_db(homes, [("conv-2", "旧名字")])
    monkeypatch.setattr(twb, "_read_back", lambda _spec, _sid: "别人的名字")

    result = twb.writeback_title("marvis", "conv-2", "新名字")

    assert result["status"] == "failed"
    assert result["ok"] is False
    assert "读回" in result["note"]
    assert result["backup"]


def test_env_switch_turns_the_whole_thing_off(homes, monkeypatch):
    """排查用的总闸：``PLOBI_TITLE_WRITEBACK=0`` ⇒ 一律只改账本。"""
    monkeypatch.setenv(twb.DISABLE_ENV, "0")
    db = _marvis_db(homes, [("conv-4", "旧名字")])

    result = twb.writeback_title("marvis", "conv-4", "新名字")

    assert result["status"] == "ledger_only"
    assert _title_in(db, "conv-4") == "旧名字"


def test_rename_through_the_agent_writes_through_and_reports_it(homes, monkeypatch):
    """整条链路：改名真写进它自己的库，账本也记，回话里带上「写回了吗」。"""
    monkeypatch.setattr(es, "_in_agent_scope", lambda *_a, **_k: True)
    db = _marvis_db(homes, [("conv-3", "旧名字")])

    result = es.update_for_agent("Sindri", "marvis", "conv-3", {"title": "跨家改名"})

    assert result["ok"] is True
    assert result["data"]["written_back"] is True
    assert "写回" in result["data"]["title_note"]
    assert _title_in(db, "conv-3") == "跨家改名"
    ledger = homes.home / "external_sessions" / "index.json"
    assert "跨家改名" in ledger.read_text(encoding="utf-8")


def test_busy_rename_leaves_the_ledger_untouched(homes, monkeypatch):
    """拦下来的改名不许悄悄记进账本——两边对不上比没改更糟。"""
    monkeypatch.setattr(es, "_in_agent_scope", lambda *_a, **_k: True)
    monkeypatch.setattr(twb, "_running", lambda _vendor: "CodeBuddy.exe")

    result = es.update_for_agent("Sindri", "codebuddy", "sess-6", {"title": "别写"})

    assert result["ok"] is False
    assert result["status"] == 409
    assert not (homes.home / "external_sessions" / "index.json").exists()


def test_summary_lists_exactly_the_seven_writable_vendors():
    """写回登记的还是 11-22 那七家（真源表多一家少一家，这条会红）。"""
    data = twb.summary()

    assert set(data["writable"]) == {
        "codebuddy",
        "kiro",
        "marvis",
        "agy",
        "opencode",
        "kilo",
        "copilot",
    }
    assert data["writable"]["codebuddy"] == "jsonl-record"
    assert data["writable"]["kiro"] == "sidecar-json"
    assert data["writable"]["marvis"] == "sqlite-column"
