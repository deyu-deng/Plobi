"""外部会话（WP-EXTERNAL-SESSIONS）：自动发现、L2 过滤、读、说。

全部用 tmp 里的假家目录，不碰这台机器上真的会话（真机读数在验收里另做）。
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


def test_an_unknown_agent_gets_a_reason_not_an_empty_shell(homes):
    payload = es.sessions_for_agent("Nobody")

    assert payload["reason"] and payload["sessions"] == []
