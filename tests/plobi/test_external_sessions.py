"""外部会话（WP-EXTERNAL-SESSIONS）：自动发现、L2 过滤、读、说。

全部用 tmp 里的假家目录，不碰这台机器上真的会话（真机读数在验收里另做）。
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from plobi.delegation import external_sessions as es  # noqa: E402


@pytest.fixture
def homes(tmp_path, monkeypatch):
    """三家各自的根 + 一个钉住的 PLOBI_HOME（L2 标记写在那里）。"""
    qoder = tmp_path / "qoder"
    codebuddy = tmp_path / "codebuddy"
    marvis = tmp_path / "marvis"
    home = tmp_path / "home"
    for directory in (qoder, codebuddy, marvis / "database", home):
        directory.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("QODER_CONFIG_DIR", str(qoder))
    monkeypatch.setenv("CODEBUDDY_CONFIG_DIR", str(codebuddy))
    monkeypatch.setenv("MARVIS_HOME", str(marvis))
    monkeypatch.setenv("CURSOR_HOME", str(tmp_path / "cursor-home"))
    monkeypatch.setenv("AGY_HOME", str(tmp_path / "agy-home"))
    monkeypatch.setenv("PLOBI_HOME", str(home))
    es._ALL_CACHE.update({"at": 0.0, "rows": []})
    es._META_CACHE.clear()

    return SimpleNamespace(qoder=qoder, codebuddy=codebuddy, marvis=marvis, home=home,
                           cursor=tmp_path / "cursor-home", agy=tmp_path / "agy-home")


def qoder_session(root: Path, project: str, session_id: str, *, prompt: str, reply: str,
                  updated: str = "2026-10-27T10:00:00.000Z") -> None:
    directory = root / "projects" / es.project_key(project)
    directory.mkdir(parents=True, exist_ok=True)
    records = [
        {"type": "workspace-directories", "sessionId": session_id, "directories": [project]},
        {"type": "user", "message": {"role": "user", "content": prompt}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "不该进 transcript"},
            {"type": "text", "text": reply},
        ]}},
    ]
    (directory / f"{session_id}.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n", encoding="utf-8")
    (directory / session_id).mkdir(exist_ok=True)
    (directory / session_id / "state.json").write_text(
        json.dumps({"sessionId": session_id, "updatedAt": updated}), encoding="utf-8")


def codebuddy_session(root: Path, cwd: str, session_id: str, *, prompt: str, reply: str,
                      stamp: int = 1791337771584) -> None:
    directory = root / "projects" / es.project_key(cwd)
    directory.mkdir(parents=True, exist_ok=True)
    records = [
        {"type": "message", "role": "user", "sessionId": session_id, "cwd": cwd, "timestamp": stamp,
         "content": [{"type": "input_text", "text": prompt}]},
        {"type": "message", "role": "assistant", "sessionId": session_id, "cwd": cwd, "timestamp": stamp + 1000,
         "content": [{"type": "output_text", "text": reply}]},
    ]
    (directory / f"{session_id}.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n", encoding="utf-8")


def marvis_session(root: Path, session_id: str, *, title: str, prompt: str, reply: str,
                   updated: str = "2026-10-27T12:00:00+00:00") -> None:
    db = root / "database" / "data.db"
    con = sqlite3.connect(db)
    con.executescript(
        "create table conversations (conversation_id text, title text, updated_at text);"
        "create table messages (conversation_id text, role text, content text, message_seq text, created_at text);"
    )
    con.execute("insert into conversations values (?,?,?)", (session_id, title, updated))
    con.executemany("insert into messages values (?,?,?,?,?)", [
        (session_id, "user", prompt, "1", updated),
        (session_id, "assistant", reply, "2", updated),
    ])
    con.commit()
    con.close()


def marvis_real_shape(root: Path, session_id: str, *, title: str, updated: str,
                      messages: list[tuple[str, str]]) -> None:
    """按**真机**的形状建一个 Marvis 库（11-08 现读）：

    - ``message_seq`` 是 INTEGER（真库是整数，假库当年写成 text，排序会按字典序）；
    - ``conversations`` 还带 ``metadata``（真库有，假库没有）；
    - 时间戳是带时区偏移的 ISO 串（``2026-11-06T01:12:29.727545300+00:00``）。
    """
    db = root / "database" / "data.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db)
    con.executescript(
        "create table conversations (conversation_id text, user_id text, title text, status text,"
        " metadata text, created_at text, updated_at text);"
        "create table messages (message_id text, conversation_id text, response_id text, role text,"
        " content text, tool_calls text, tool_call_id text, tool_name text, message_seq integer,"
        " event_seq_anchor integer, metadata text, created_at text);"
    )
    con.execute("insert into conversations values (?,?,?,?,?,?,?)",
                (session_id, "", title, "completed", "{}", updated, updated))
    for index, (role, content) in enumerate(messages, start=1):
        con.execute("insert into messages values (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (f"msg_{index}", session_id, "", role, content, None, None, None,
                     index, None, "{}", updated))
    con.commit()
    con.close()


def cursor_only_drafts(root: Path) -> None:
    """Cursor 的库在，但里面**只有草稿与已归档**（11-08 这台机器上的真形状）。"""
    base = root / "User"
    (base / "globalStorage").mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(base / "globalStorage" / "state.vscdb")
    con.executescript(
        "create table composerHeaders (composerId text, workspaceId text, createdAt integer,"
        " lastUpdatedAt integer, isArchived integer);"
        "create table cursorDiskKV (key text primary key, value text);"
    )
    con.execute("insert into composerHeaders values (?,?,?,?,0)", ("empty-state-draft", "ws-empty", 1, 1))
    con.execute("insert into composerHeaders values (?,?,?,?,1)", ("899631c0-fbf9", "ws-empty", 2, 2))
    con.execute("insert into cursorDiskKV values (?,?)",
                ("composerData:899631c0-fbf9",
                 json.dumps({"conversationMap": {}, "fullConversationHeadersOnly": []})))
    con.commit()
    con.close()


def stub_registry(monkeypatch, name: str, project: str):
    class _Registry:
        agents = {name: SimpleNamespace(name=name, display_name="", project_path=project, project_paths=(project,))}

        @classmethod
        def load(cls):
            return cls()

    import plobi.agents.registry as registry_mod

    monkeypatch.setattr(registry_mod, "AgentRegistry", _Registry)


def cursor_session(root: Path, workspace_id: str, session_id: str, *, prompt: str, reply: str,
                   project: str, stamp: int = 1791337771584) -> None:
    base = root / "User"
    (base / "globalStorage").mkdir(parents=True, exist_ok=True)
    (base / "workspaceStorage" / workspace_id).mkdir(parents=True, exist_ok=True)
    (base / "workspaceStorage" / workspace_id / "workspace.json").write_text(
        json.dumps({"folder": project}), encoding="utf-8")
    con = sqlite3.connect(base / "globalStorage" / "state.vscdb")
    con.executescript(
        "create table composerHeaders (composerId text, workspaceId text, createdAt integer,"
        " lastUpdatedAt integer, isArchived integer);"
        "create table cursorDiskKV (key text primary key, value text);"
    )
    con.execute("insert into composerHeaders values (?,?,?,?,0)", (session_id, workspace_id, stamp, stamp))
    data = {
        "composerId": session_id,
        "conversationMap": {
            "b1": {"type": 1, "text": prompt},
            "b2": {"type": 2, "text": reply},
        },
        "fullConversationHeadersOnly": [{"bubbleId": "b1"}, {"bubbleId": "b2"}],
    }
    con.execute("insert into cursorDiskKV values (?,?)", (f"composerData:{session_id}", json.dumps(data)))
    con.commit()
    con.close()


def agy_session(root: Path, session_id: str, *, title: str, project_uri: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(root / "conversation_summaries.db")
    con.executescript(
        "create table conversation_summaries (conversation_id text, title text, preview text,"
        " last_modified_time text, workspace_uris text);"
    )
    con.execute("insert into conversation_summaries values (?,?,?,?,?)",
                (session_id, title, title, "2026-10-10 10:29:41.3721344+00:00", json.dumps([project_uri])))
    con.commit()
    con.close()


def agy_transcript(root: Path, session_id: str, *, prompt: str, reply: str) -> None:
    """agy 把对话放在 ``brain/<会话号>/.system_generated/logs/transcript.jsonl``。"""
    logs = root / "brain" / session_id / ".system_generated" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    wrapped = f"<USER_REQUEST>\n{prompt}\n</USER_REQUEST>\n<USER_SETTINGS_CHANGE>无关的一段</USER_SETTINGS_CHANGE>"
    records = [
        {"step_index": 0, "type": "USER_INPUT", "created_at": "2026-10-10T06:36:51Z", "content": wrapped},
        {"step_index": 1, "type": "PLANNER_RESPONSE", "content": "None"},
        {"step_index": 2, "type": "GENERIC", "content": "工具输出不是对话"},
        {"step_index": 3, "type": "PLANNER_RESPONSE", "content": reply},
    ]
    (logs / "transcript.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n", encoding="utf-8")


def test_the_project_key_is_the_absolute_path_with_punctuation_flattened():
    assert es.project_key(r"D:\Projects\Sindri") == "D--Projects-Sindri"
    assert es.project_key("D:/Projects/Sindri") == "D--Projects-Sindri"


def test_discovery_finds_every_vendor_on_this_machine(homes):
    qoder_session(homes.qoder, r"D:\Projects\Sindri", "aaaaaaaa-1111", prompt="先读 README", reply="读完了")
    codebuddy_session(homes.codebuddy, r"D:\Temp", "01a1140d-30ed", prompt="写个函数", reply="写好了")
    marvis_session(homes.marvis, "conv_00b52c85", title="Computor", prompt="Computor", reply="你是想说 Computer 吗")
    cursor_session(homes.cursor, "ws1", "899631c0-fbf9", prompt="给 Sindri 写脚本", reply="写好了",
                   project="file:///d%3A/Projects/Sindri")
    agy_session(homes.agy, "ca174f50-f11a", title="Simple Greeting", project_uri="file:///D:/Temp")

    found = {item["vendor"] for item in es.all_sessions()}
    assert found == {"qoder", "codebuddy", "marvis", "cursor", "agy"}

    sources = {item["vendor"]: item for item in es.session_sources()}
    assert [sources[v]["sessions"] for v in ("qoder", "codebuddy", "marvis", "cursor", "agy")] == [1, 1, 1, 1, 1]
    assert sources["cursor"]["readable"] is True
    assert sources["agy"]["readable"] is True


def test_cursor_transcript_comes_from_the_composer_map(homes):
    cursor_session(homes.cursor, "ws1", "899631c0-fbf9", prompt="给 Sindri 写脚本", reply="写好了",
                   project="file:///d%3A/Projects/Sindri")

    out = es.read_transcript("cursor", "899631c0-fbf9")

    assert out["ok"] is True
    assert [m["role"] for m in out["data"]["messages"]] == ["user", "assistant"]
    assert out["data"]["messages"][1]["text"] == "写好了"
    assert out["data"]["project"] == r"D:\Projects\Sindri"


def test_agy_gives_titles_and_its_transcript(homes):
    agy_session(homes.agy, "ca174f50-f11a", title="Simple Greeting", project_uri="file:///D:/Temp")

    rows = {item["id"]: item for item in es.all_sessions()}
    assert rows["ca174f50-f11a"]["title"] == "Simple Greeting"
    assert rows["ca174f50-f11a"]["project"] == r"D:\Temp"

    # 正文在 brain/<会话号>/.system_generated/logs/transcript.jsonl 里，读得到
    agy_transcript(homes.agy, "ca174f50-f11a", prompt="Hello", reply="Hi")
    out = es.read_transcript("agy", "ca174f50-f11a")
    assert out["ok"] is True
    assert [m["role"] for m in out["data"]["messages"]] == ["user", "assistant"]


def test_related_comes_from_path_then_title_then_l2_marks(homes, monkeypatch):
    qoder_session(homes.qoder, r"D:\Projects\Sindri", "aaaaaaaa-1111", prompt="先读 README", reply="读完了")
    codebuddy_session(homes.codebuddy, r"D:\Temp", "01a1140d-30ed", prompt="给 Sindri 写个脚本", reply="写好了")
    marvis_session(homes.marvis, "conv_00b52c85", title="Computor", prompt="Computor", reply="嗯")
    stub_registry(monkeypatch, "Sindri", r"D:\Projects\Sindri")

    rows = {f"{item['vendor']}:{item['id']}": item for item in es.sessions_for_agent("Sindri")["sessions"]}

    assert rows["qoder:aaaaaaaa-1111"]["related"] is True
    assert rows["qoder:aaaaaaaa-1111"]["related_source"] == "path"
    assert rows["codebuddy:01a1140d-30ed"]["related"] is True
    assert rows["codebuddy:01a1140d-30ed"]["related_source"] == "title"
    assert rows["marvis:conv_00b52c85"]["related"] is None
    assert rows["marvis:conv_00b52c85"]["related_source"] == "unknown"


def test_a_session_in_another_directory_is_filtered_out_not_left_unknown(homes, monkeypatch):
    qoder_session(homes.qoder, r"D:\Projects\Plobi", "bbbbbbbb-2222", prompt="看一下额度", reply="看完了")
    stub_registry(monkeypatch, "Sindri", r"D:\Projects\Sindri")

    rows = {item["id"]: item for item in es.sessions_for_agent("Sindri")["sessions"]}

    assert rows["bbbbbbbb-2222"]["related"] is False
    assert rows["bbbbbbbb-2222"]["related_source"] == "path-miss"


def test_l2_marks_override_the_auto_score_and_are_written_once(homes, monkeypatch):
    marvis_session(homes.marvis, "conv_00b52c85", title="Computor", prompt="Computor", reply="嗯")
    stub_registry(monkeypatch, "Sindri", r"D:\Projects\Sindri")

    assert es.mark_for_agent("Sindri", [{"vendor": "marvis", "id": "conv_00b52c85", "related": True}])["data"]["written"] == 1

    rows = {item["id"]: item for item in es.sessions_for_agent("Sindri")["sessions"]}
    assert rows["conv_00b52c85"]["related"] is True
    assert rows["conv_00b52c85"]["related_source"] == "l2"
    assert json.loads((homes.home / "external_sessions" / "relevance.json").read_text(encoding="utf-8"))


def test_transcripts_read_dialogue_and_drop_tool_noise(homes):
    qoder_session(homes.qoder, r"D:\Projects\Sindri", "aaaaaaaa-1111", prompt="先读 README", reply="读完了")
    codebuddy_session(homes.codebuddy, r"D:\Temp", "01a1140d-30ed", prompt="写个函数", reply="写好了")
    marvis_session(homes.marvis, "conv_00b52c85", title="Computor", prompt="Computor", reply="你是想说 Computer 吗")

    qoder = es.read_transcript("qoder", "aaaaaaaa-1111")
    codebuddy = es.read_transcript("codebuddy", "01a1140d-30ed")
    marvis = es.read_transcript("marvis", "conv_00b52c85")

    assert [m["role"] for m in qoder["data"]["messages"]] == ["user", "assistant"]
    assert "不该进" not in json.dumps(qoder, ensure_ascii=False)
    assert codebuddy["data"]["messages"][1]["text"] == "写好了"
    assert marvis["data"]["messages"][0]["text"] == "Computor"


@pytest.mark.parametrize("bad", ["../secret", "", "a/b", "x" * 200])
def test_a_session_id_that_is_not_a_name_never_reaches_the_filesystem(homes, bad):
    assert es.read_transcript("qoder", bad)["ok"] is False


def test_a_marked_false_session_leaves_the_agents_scope(homes, monkeypatch):
    marvis_session(homes.marvis, "conv_00b52c85", title="Computor", prompt="Computor", reply="嗯")
    stub_registry(monkeypatch, "Sindri", r"D:\Projects\Sindri")
    es.mark_for_agent("Sindri", [{"vendor": "marvis", "id": "conv_00b52c85", "related": False}])

    assert es.transcript_for_agent("Sindri", "marvis", "conv_00b52c85")["ok"] is False


def test_sending_is_qoder_only_and_resumes_with_scrubbed_env(homes, monkeypatch):
    qoder_session(homes.qoder, r"D:\Projects\Sindri", "aaaaaaaa-1111", prompt="先读 README", reply="读完了")
    marvis_session(homes.marvis, "conv_00b52c85", title="Computor", prompt="Computor", reply="嗯")
    stub_registry(monkeypatch, "Sindri", r"D:\Projects\Sindri")
    es.mark_for_agent("Sindri", [{"vendor": "marvis", "id": "conv_00b52c85", "related": True}])

    assert es.send_for_agent("Sindri", "marvis", "conv_00b52c85", "你好")["ok"] is False

    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = list(argv)
        seen["env"] = kwargs["env"]
        seen["cwd"] = kwargs["cwd"]
        return SimpleNamespace(returncode=0, stdout=json.dumps({
            "type": "result", "result": "又是那行", "total_cost_usd": 0, "total_credits": 1.25, "usage": {},
        }), stderr="")

    from agent import external_offload as channel

    monkeypatch.setattr(channel, "resolve_command", lambda _a="": "D:/bin/qodercli.cmd")
    monkeypatch.setattr(channel, "cwd_is_allowed", lambda _p: (True, ""))
    monkeypatch.setattr(channel.subprocess, "run", fake_run)
    monkeypatch.setenv("QODER_AGENT_SDK_ENTRYPOINT", "1")

    out = es.send_for_agent("Sindri", "qoder", "aaaaaaaa-1111", "标题是什么？")

    assert out["ok"] is True, out
    assert seen["argv"][1:3] == ["--resume", "aaaaaaaa-1111"]
    assert seen["argv"][-2:] == ["-p", "标题是什么？"]
    assert seen["cwd"] == r"D:\Projects\Sindri"
    assert "QODER_AGENT_SDK_ENTRYPOINT" not in seen["env"]
    assert out["data"]["reply"] == "又是那行" and out["data"]["credits"] == 1.25


def test_an_empty_message_is_refused_before_anything_is_spawned(homes, monkeypatch):
    qoder_session(homes.qoder, r"D:\Projects\Sindri", "aaaaaaaa-1111", prompt="先读 README", reply="读完了")
    stub_registry(monkeypatch, "Sindri", r"D:\Projects\Sindri")

    assert es.send_for_agent("Sindri", "qoder", "aaaaaaaa-1111", "   ")["ok"] is False


def test_agy_transcript_reads_the_conversation_from_its_brain(homes):
    agy_session(homes.agy, "b728e107-050e", title="Fib", project_uri="file:///D:/Temp")
    agy_transcript(homes.agy, "b728e107-050e", prompt="写个 fib.py", reply="写好了，fib.py 就在目录里")

    out = es.read_transcript("agy", "b728e107-050e")

    assert out["ok"] is True
    assert [m["role"] for m in out["data"]["messages"]] == ["user", "assistant"]
    assert out["data"]["messages"][0]["text"] == "写个 fib.py"
    assert out["data"]["messages"][1]["text"] == "写好了，fib.py 就在目录里"
    # 工具输出与 "None" 不算对话
    assert "工具输出不是对话" not in json.dumps(out, ensure_ascii=False)


def test_agy_is_now_readable_in_the_sources_list(homes):
    agy_session(homes.agy, "ca174f50-f11a", title="Simple Greeting", project_uri="file:///D:/Temp")

    sources = {item["vendor"]: item for item in es.session_sources()}

    assert sources["agy"]["readable"] is True
    assert sources["agy"]["sessions"] == 1


# ── 管：改名 / 置顶 / 归档 / 删除（回收站）────────────────────────────────

def test_rename_is_ours_only_and_shows_in_the_list(homes, monkeypatch):
    qoder_session(homes.qoder, r"D:\Projects\Sindri", "aaaaaaaa-1111", prompt="先读 README", reply="读完了")
    stub_registry(monkeypatch, "Sindri", r"D:\Projects\Sindri")

    assert es.update_for_agent("Sindri", "qoder", "aaaaaaaa-1111", {"title": "课本那一节"})["ok"] is True

    rows = {item["id"]: item for item in es.sessions_for_agent("Sindri")["sessions"]}
    assert rows["aaaaaaaa-1111"]["title"] == "课本那一节"
    # 原文件一个字没动：我们只在账本里改名
    assert "先读 README" in (homes.qoder / "projects" / es.project_key(r"D:\Projects\Sindri") / "aaaaaaaa-1111.jsonl").read_text(encoding="utf-8")


def test_pinned_goes_first_and_archived_leaves_the_active_list(homes, monkeypatch):
    qoder_session(homes.qoder, r"D:\Projects\Sindri", "aaaaaaaa-1111", prompt="旧的一条", reply="好",
                  updated="2026-10-01T09:00:00.000Z")
    qoder_session(homes.qoder, r"D:\Projects\Sindri", "bbbbbbbb-2222", prompt="新的一条", reply="好",
                  updated="2026-10-27T09:00:00.000Z")
    stub_registry(monkeypatch, "Sindri", r"D:\Projects\Sindri")

    es.update_for_agent("Sindri", "qoder", "aaaaaaaa-1111", {"pinned": True})
    es.update_for_agent("Sindri", "qoder", "bbbbbbbb-2222", {"archived": True})

    rows = es.sessions_for_agent("Sindri")["sessions"]
    assert rows[0]["id"] == "aaaaaaaa-1111" and rows[0]["pinned"] is True
    assert [row["id"] for row in rows] == ["aaaaaaaa-1111", "bbbbbbbb-2222"]
    assert rows[1]["archived"] is True


def test_delete_moves_the_session_to_trash_and_restore_brings_it_back(homes, monkeypatch):
    qoder_session(homes.qoder, r"D:\Projects\Sindri", "aaaaaaaa-1111", prompt="先读 README", reply="读完了")
    stub_registry(monkeypatch, "Sindri", r"D:\Projects\Sindri")
    directory = homes.qoder / "projects" / es.project_key(r"D:\Projects\Sindri")

    out = es.delete_for_agent("Sindri", "qoder", "aaaaaaaa-1111")

    assert out["ok"] is True
    assert not (directory / "aaaaaaaa-1111.jsonl").exists()
    trash = list((homes.home / "external_sessions" / "trash" / "qoder").rglob("aaaaaaaa-1111.jsonl"))
    assert trash and trash[0].read_text(encoding="utf-8")
    assert es.sessions_for_agent("Sindri")["sessions"] == []

    assert es.restore_for_agent("Sindri", "qoder", "aaaaaaaa-1111")["ok"] is True
    assert (directory / "aaaaaaaa-1111.jsonl").exists()
    assert [row["id"] for row in es.sessions_for_agent("Sindri")["sessions"]] == ["aaaaaaaa-1111"]


def test_a_database_vendor_is_hidden_not_moved(homes, monkeypatch):
    marvis_session(homes.marvis, "conv_00b52c85", title="Computor", prompt="Computor", reply="嗯")
    stub_registry(monkeypatch, "Sindri", r"D:\Projects\Sindri")
    es.mark_for_agent("Sindri", [{"vendor": "marvis", "id": "conv_00b52c85", "related": True}])
    db = homes.marvis / "database" / "data.db"
    before = db.read_bytes()

    out = es.delete_for_agent("Sindri", "marvis", "conv_00b52c85")

    assert out["ok"] is True and out["data"]["deleted"]["hid_only"] is True
    assert db.read_bytes() == before, "数据库那家的库一个字节都不许动"
    assert es.sessions_for_agent("Sindri")["sessions"] == []


def test_a_session_outside_the_agents_scope_cannot_be_deleted(homes, monkeypatch):
    qoder_session(homes.qoder, r"D:\Projects\Other", "cccccccc-3333", prompt="别处", reply="好")
    stub_registry(monkeypatch, "Sindri", r"D:\Projects\Sindri")
    es.mark_for_agent("Sindri", [{"vendor": "qoder", "id": "cccccccc-3333", "related": False}])

    assert es.delete_for_agent("Sindri", "qoder", "cccccccc-3333")["ok"] is False


def test_an_unknown_agent_gets_a_reason_not_an_empty_shell(homes):
    payload = es.sessions_for_agent("Nobody")

    assert payload["reason"] and payload["sessions"] == []


# ── Marvis / Cursor：照实那一面（WP-OFFLOAD-SESSION-MARVIS，11-08 现读）──────

def test_marvis_shows_the_first_sentence_and_keeps_the_project_name_apart(homes):
    marvis_real_shape(
        homes.marvis, "conv_87a0e49f40ae4", title="Quota",
        updated="2026-11-06T01:12:29.727545300+00:00",
        messages=[
            ("user", "你看 Project 里面有一个 Plobi 项目。\n第二行不进标题"),
            ("assistant", "好"),
        ])

    row = {item["id"]: item for item in es.all_sessions()}["conv_87a0e49f40ae4"]

    assert row["title"] == "你看 Project 里面有一个 Plobi 项目。"
    # Marvis 自己的会话名（多半是项目名）不丢，另放一格
    assert row["source_title"] == "Quota"
    # Marvis 不记这条会话在哪个目录 ⇒ 留空，**不猜**
    assert row["project"] == ""


def test_marvis_time_is_local_so_the_list_sorts_right(homes):
    stamp = "2026-11-06T01:12:29.727545300+00:00"
    marvis_real_shape(homes.marvis, "conv_1", title="Quota", updated=stamp,
                      messages=[("user", "在吗")])

    row = {item["id"]: item for item in es.all_sessions()}["conv_1"]
    expected = datetime.fromisoformat(stamp).astimezone().strftime("%Y-%m-%dT%H:%M:%S")

    assert row["updated_at"] == expected
    assert "+00:00" not in row["updated_at"]


def test_the_list_orders_two_vendors_by_real_time_not_by_string(homes):
    """两家的时钟形状不一样时，排序不能靠字符串瞎比（带时区的那一截会把序比反）。"""
    qoder_session(homes.qoder, r"D:\Projects\Sindri", "aaaaaaaa-1111", prompt="先读 README",
                  reply="读完了", updated="2026-11-06T05:00:00Z")  # 本地 13:00
    marvis_real_shape(homes.marvis, "conv_1", title="Quota",
                      updated="2026-11-06T01:12:29+00:00",  # 本地 09:12
                      messages=[("user", "在吗")])

    order = [item["vendor"] for item in es.all_sessions()]

    assert order.index("qoder") < order.index("marvis")


def test_a_huge_message_is_cut_and_says_so(homes):
    marvis_real_shape(homes.marvis, "conv_long", title="Quota",
                      updated="2026-11-06T01:12:29+00:00",
                      messages=[("user", "长" * 31582), ("assistant", "好")])

    out = es.read_transcript("marvis", "conv_long")
    text = out["data"]["messages"][0]["text"]

    assert len(text) == es.MAX_TRANSCRIPT_CHARS + len(es.TRUNCATED_MARK)
    assert text.endswith(es.TRUNCATED_MARK)


def test_a_long_conversation_gives_back_only_the_last_chunk(homes):
    marvis_real_shape(homes.marvis, "conv_many", title="Quota",
                      updated="2026-11-06T01:12:29+00:00",
                      messages=[("user", f"第 {index} 句") for index in range(400)])

    out = es.read_transcript("marvis", "conv_many")
    messages = out["data"]["messages"]

    assert len(messages) == es.MAX_MESSAGES
    assert messages[-1]["text"] == "第 399 句"
    assert messages[0]["text"] == "第 100 句"


def test_cursor_says_it_left_nothing_behind(homes):
    """Cursor：库在，但没有可读的对话 ⇒ 照实说「这台没留下」，不假装读得到。"""
    cursor_only_drafts(homes.cursor)

    rows = [item for item in es.all_sessions() if item["vendor"] == "cursor"]
    sources = {item["vendor"]: item for item in es.session_sources()}

    assert rows == []
    assert sources["cursor"]["readable"] is False
    assert "没留下" in sources["cursor"]["reason"]
    assert sources["cursor"]["sessions"] == 0


def test_reading_a_vendor_library_never_writes_to_it(homes):
    marvis_real_shape(homes.marvis, "conv_1", title="Quota",
                      updated="2026-11-06T01:12:29+00:00", messages=[("user", "在吗")])
    cursor_only_drafts(homes.cursor)
    marvis_db = homes.marvis / "database" / "data.db"
    cursor_db = homes.cursor / "User" / "globalStorage" / "state.vscdb"
    before = (marvis_db.read_bytes(), cursor_db.read_bytes())

    es.all_sessions()
    es.read_transcript("marvis", "conv_1")

    assert (marvis_db.read_bytes(), cursor_db.read_bytes()) == before
