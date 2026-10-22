"""``plobi say`` —— 外包/终端那一侧的把手，测的是「它有没有偷走第二条路」。

这一档存在的理由：被外包出去的 L2 跑在外部软件自己的进程里，拿不到 ``say`` 这枚模型工具，
于是干完活开不了口。补的是**把手**，所以判据全围绕一件事：**投递必须仍然只有
``channel.handle_say`` 那一条路**（裁 94）。这里因此**不打桩 handle_say**——真跑它，
只把名册与 SessionDB 换成假的、把叫醒换成 no-op，看消息到底落进了谁的会话。

另两条是安全形状，不是风格：说话者身份只认 ``PLOBI_AGENT_ID``（命令行上能自报身份
= 谁都能冒充别的 Agent），失败必须给非零退出码（把手是给脚本用的，假成功会被当成送到）。
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[2]
PLUGIN_DIR = REPO / "plugins" / "plobi-north-star"


@pytest.fixture(scope="module")
def NS():
    """按包方式加载整个插件（``channel`` 与 ``cli_say`` 都有相对导入）。"""
    pkg_name = "plobi_test_say_cli_pkg"
    if pkg_name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            pkg_name, PLUGIN_DIR / "__init__.py", submodule_search_locations=[str(PLUGIN_DIR)]
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        module.__package__ = pkg_name
        module.__path__ = [str(PLUGIN_DIR)]
        sys.modules[pkg_name] = module
        spec.loader.exec_module(module)
    return importlib.import_module(pkg_name)


@pytest.fixture(scope="module")
def CH(NS):
    return importlib.import_module(f"{NS.__name__}.channel")


@pytest.fixture()
def cli(NS):
    return NS.cli_say


class _FakeSessionDB:
    def __init__(self):
        self.appended: list[tuple] = []
        self.created: list[tuple] = []

    def latest_conversational_session_for_agent(self, agent_id):
        return None

    def create_session(self, session_id, *, source=None, agent_id=None):
        self.created.append((session_id, source, agent_id))
        return session_id

    def append_message(self, session_id, *, role=None, content=None, asked_by=None):
        self.appended.append((session_id, role, content, asked_by))
        return f"m_{len(self.appended)}"


def _roster_of(*names: str):
    from plobi.agents.registry import AgentEntry

    agents = {
        name: AgentEntry.from_dict(name, {"role": "l2_project", "category": "projects"})
        for name in names
    }

    class _Registry:
        def get(self, name):
            return agents.get(name)

        def __init__(self, rows):
            self.agents = rows

    return _Registry(agents)


@pytest.fixture()
def wired(CH, monkeypatch, tmp_path):
    """真 handle_say + 假名册/假库/不叫醒。返回那只假库。"""
    fake = _FakeSessionDB()
    monkeypatch.setattr(CH, "_roster", lambda: _roster_of("Sindri", "Plobi"))
    monkeypatch.setattr(CH, "_session_db", lambda: fake)
    monkeypatch.setattr(CH, "wake_agent", lambda *a, **k: (False, "测试里不叫醒"))
    monkeypatch.setattr(CH, "trace_path", lambda: tmp_path / "say-trace.jsonl")
    return fake


def _parser(cli):
    p = argparse.ArgumentParser(prog="plobi say")
    cli.setup(p)
    return p


# ─── 一条真源：把手走的就是说那一枚 ──────────────────────────────────────

def test_cli_delivers_into_the_recipient_own_session(cli, CH, wired, monkeypatch):
    monkeypatch.setenv("PLOBI_AGENT_ID", "Plobi")
    args = _parser(cli).parse_args(["--to", "Sindri", "--text", "课表我采完了，两条没课"])

    assert cli.run(args) == 0
    session_id, role, content, asked_by = wired.appended[0]
    assert asked_by == "Plobi", "回执里的说话者就是身份，不是命令行上填出来的"
    assert content.startswith("[来自 Plobi · "), "留痕的头与 say 工具逐字相同"
    assert "课表我采完了" in content
    assert session_id.startswith("chan_"), "对方没有可见会话时开一条，而不是落进自己家"


def test_cli_speaker_comes_from_env_only(cli, CH, wired, monkeypatch):
    monkeypatch.setenv("PLOBI_AGENT_ID", "Aura")
    cli.run(_parser(cli).parse_args(["--to", "Sindri", "--text", "hi"]))
    assert wired.appended[0][3] == "Aura"

    monkeypatch.delenv("PLOBI_AGENT_ID", raising=False)
    monkeypatch.delenv("PLOBI_AGENT", raising=False)
    cli.run(_parser(cli).parse_args(["--to", "Sindri", "--text", "hi again"]))
    assert wired.appended[-1][3] == CH.current_agent_name() == "plobi"


def test_no_way_to_self_report_the_speaker(cli):
    """``--as`` 必须解析失败：能自报身份 = 谁都能冒充别的 Agent 说话。"""
    with pytest.raises(SystemExit):
        _parser(cli).parse_args(["--to", "Sindri", "--text", "hi", "--as", "Sindri"])


# ─── parts 的形状与 say 工具一致（裁 183③：指针不读内容）─────────────────

def test_repeated_text_and_path_keep_order_as_parts(cli, CH, wired, monkeypatch, tmp_path):
    monkeypatch.setenv("PLOBI_AGENT_ID", "Plobi")
    victim = tmp_path / "notes.md"
    victim.write_text("这串字不该被贴进消息里", encoding="utf-8")

    args = _parser(cli).parse_args([
        "--to", "Sindri", "--text", "第一段", "--text", "第二段", "--path", str(victim),
    ])
    assert cli.run(args) == 0
    content = wired.appended[0][2]
    assert content.index("第一段") < content.index("第二段") < content.index("[文件]")
    assert "这串字不该被贴进消息里" not in content, "文件指针只留路径，内容让对方自己读"


# ─── 失败不许伪装成成功 ──────────────────────────────────────────────────

def test_unknown_recipient_exits_nonzero_and_lists_who_is_reachable(cli, wired, capsys):
    args = _parser(cli).parse_args(["--to", "没有这个人", "--text", "hi"])

    assert cli.run(args) == 1
    out = capsys.readouterr().out
    assert "没送出去" in out
    assert "Sindri" in out, "拒绝时要把可发的人列出来，不许让人猜名字"
    assert wired.appended == [], "解析不到收件人 ⇒ 库里一个字都不许写"


def test_empty_message_exits_nonzero_without_touching_the_db(cli, wired, capsys):
    assert cli.run(_parser(cli).parse_args(["--to", "Sindri"])) == 1
    assert "没内容" in capsys.readouterr().out
    assert wired.appended == []


def test_json_flag_prints_the_same_receipt_the_tool_returns(cli, wired, capsys, monkeypatch):
    monkeypatch.setenv("PLOBI_AGENT_ID", "Plobi")
    args = _parser(cli).parse_args(["--to", "Sindri", "--text", "hi", "--json"])

    assert cli.run(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True and payload["mode"] == "say"
    assert payload["to"] == "Sindri" and payload["deliverer"] == "Plobi"
