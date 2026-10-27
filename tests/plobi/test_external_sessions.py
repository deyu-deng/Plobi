"""外部会话（WP-EXTERNAL-SESSIONS）的三条判据：列、读、说。

全部用 tmp 里的假 Qoder 目录，不碰这台机器上真的会话（真机只读一次在验收里做）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from plobi.delegation import external_sessions as es  # noqa: E402


@pytest.fixture
def qoder(tmp_path, monkeypatch):
    """一个假的 ~/.qoder：``projects/<键>/<会话号>.jsonl`` + ``<会话号>/state.json``。"""
    root = tmp_path / "qoder"
    root.mkdir()
    monkeypatch.setenv("QODER_CONFIG_DIR", str(root))
    return root


def make_session(root: Path, project: str, session_id: str, *, prompt: str, reply: str,
                 updated: str = "2026-10-27T10:00:00.000Z") -> Path:
    directory = root / "projects" / es.project_key(project)
    directory.mkdir(parents=True, exist_ok=True)
    jsonl = directory / f"{session_id}.jsonl"
    records = [
        {"type": "workspace-directories", "value": [project]},
        {"type": "user", "message": {"role": "user", "content": prompt}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "不该进 transcript"},
            {"type": "text", "text": reply},
        ]}},
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "content": "工具结果也不该进"},
        ]}},
    ]
    jsonl.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n", encoding="utf-8")
    (directory / session_id).mkdir(exist_ok=True)
    (directory / session_id / "state.json").write_text(
        json.dumps({"sessionId": session_id, "createdAt": updated, "updatedAt": updated}), encoding="utf-8"
    )

    return jsonl


def test_the_project_key_is_the_absolute_path_with_punctuation_flattened():
    assert es.project_key(r"D:\Projects\Sindri") == "D--Projects-Sindri"
    assert es.project_key("D:/Projects/Sindri") == "D--Projects-Sindri"


def test_only_sessions_under_the_agents_own_projects_are_listed(qoder):
    make_session(qoder, r"D:\Projects\Sindri", "aaaaaaaa-1111", prompt="先读 README", reply="读完了",
                 updated="2026-10-27T09:00:00.000Z")
    make_session(qoder, r"D:\Projects\Sindri", "bbbbbbbb-2222", prompt="第二条", reply="好",
                 updated="2026-10-27T11:00:00.000Z")
    make_session(qoder, r"D:\Projects\Other", "cccccccc-3333", prompt="别处", reply="不该出现")

    payload = es.list_sessions([r"D:\Projects\Sindri"])

    ids = [item["id"] for item in payload["sessions"]]
    assert ids == ["bbbbbbbb-2222", "aaaaaaaa-1111"], "新的在前，且只列这个项目的"
    assert payload["sessions"][0]["title"] == "第二条"
    assert payload["sessions"][0]["updated_at"].startswith("2026-10-27T11:00")


def test_an_empty_list_says_why_instead_of_an_empty_shell(qoder):
    payload = es.list_sessions([r"D:\Projects\Nothing"])

    assert payload["sessions"] == [] and payload["reason"]
    assert es.list_sessions([])["reason"], "没登记目录也要有一句原因"


def test_the_transcript_keeps_dialogue_and_drops_tool_noise(qoder):
    make_session(qoder, r"D:\Projects\Sindri", "aaaaaaaa-1111", prompt="先读 README", reply="读完了")

    out = es.read_transcript([r"D:\Projects\Sindri"], "aaaaaaaa-1111")

    assert out["ok"] is True
    assert [m["role"] for m in out["data"]["messages"]] == ["user", "assistant"]
    assert out["data"]["messages"][0]["text"] == "先读 README"
    assert out["data"]["messages"][1]["text"] == "读完了"
    assert "不该进" not in json.dumps(out, ensure_ascii=False)


@pytest.mark.parametrize("bad", ["../secret", "", "a/b", "x" * 200])
def test_a_session_id_that_is_not_a_name_never_reaches_the_filesystem(qoder, bad):
    assert es.read_transcript([r"D:\Projects\Sindri"], bad)["ok"] is False


def test_a_session_of_another_agent_is_not_readable(qoder):
    make_session(qoder, r"D:\Projects\Other", "cccccccc-3333", prompt="别处", reply="不该出现")

    assert es.read_transcript([r"D:\Projects\Sindri"], "cccccccc-3333")["ok"] is False


def test_sending_resumes_that_session_and_scrubs_the_sdk_env(qoder, monkeypatch):
    make_session(qoder, r"D:\Projects\Sindri", "aaaaaaaa-1111", prompt="先读 README", reply="读完了")
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = list(argv)
        seen["env"] = kwargs["env"]
        seen["cwd"] = kwargs["cwd"]
        return SimpleNamespace(returncode=0, stdout=json.dumps({
            "type": "result", "result": "又是那行", "session_id": "aaaaaaaa-1111",
            "total_cost_usd": 0, "total_credits": 1.25, "usage": {},
        }), stderr="")

    from agent import external_offload as channel

    monkeypatch.setattr(channel, "resolve_command", lambda _a="": "D:/bin/qodercli.cmd")
    monkeypatch.setattr(channel, "cwd_is_allowed", lambda _p: (True, ""))
    monkeypatch.setattr(channel.subprocess, "run", fake_run)
    monkeypatch.setenv("QODER_AGENT_SDK_ENTRYPOINT", "1")

    out = es.send_message([r"D:\Projects\Sindri"], "aaaaaaaa-1111", "标题是什么？")

    assert out["ok"] is True, out
    assert seen["argv"][1:3] == ["--resume", "aaaaaaaa-1111"]
    assert seen["argv"][-2:] == ["-p", "标题是什么？"]
    assert seen["cwd"] == r"D:\Projects\Sindri"
    assert "QODER_AGENT_SDK_ENTRYPOINT" not in seen["env"]
    assert out["data"]["reply"] == "又是那行"
    assert out["data"]["credits"] == 1.25


def test_an_empty_message_is_refused_before_anything_is_spawned(qoder, monkeypatch):
    from agent import external_offload as channel

    monkeypatch.setattr(channel.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("不该起进程")))

    assert es.send_message([r"D:\Projects\Sindri"], "aaaaaaaa-1111", "   ")["ok"] is False


def test_sessions_for_an_agent_use_its_registry_paths(qoder, monkeypatch):
    make_session(qoder, r"D:\Projects\Sindri", "aaaaaaaa-1111", prompt="先读 README", reply="读完了")

    class _Registry:
        agents = {"Sindri": SimpleNamespace(project_path=r"D:\Projects\Sindri", project_paths=(r"D:\Projects\Sindri",))}

        @classmethod
        def load(cls):
            return cls()

    import plobi.agents.registry as registry_mod

    monkeypatch.setattr(registry_mod, "AgentRegistry", _Registry)

    payload = es.sessions_for_agent("Sindri")

    assert payload["projects"] == [r"D:\Projects\Sindri"]
    assert [item["id"] for item in payload["sessions"]] == ["aaaaaaaa-1111"]
    assert es.sessions_for_agent("Nobody")["reason"]
