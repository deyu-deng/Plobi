"""外部会话有主了，而且**可以有好几个主**（`WP-EXTERNAL-THREAD-OWNER` §4.1 一族）。

这一档钉的是四笔改造的**判据**，不是实现细节：

1. **多归属**：`owners=["A","B"]` ⇒ A、B 都拿 ``True``，不在名单里的人拿 ``False``；
   只写一个人 ⇒ 其余人 ``False``；名单是空的 ⇒ 谁拿都是 ``None``（判过了但无主）；
2. **兼容早期单值形**：老账本里的 ``owner="A"`` 当 ``[A]`` 读、``owner=""`` 当 ``[]`` 读，
   不迁移、不报错；工具参数传字符串也认（内部包成数组）；
3. **名字查不到不许落脏数据**：名单里有人不在名册 ⇒ **整条**拒掉、逐个点名；
   账本里有、名册里已经没有 ⇒ 逐个剔掉，全剔光按 ``None`` 且带一句人话（不藏会话）；
4. **派活能续上，也会记名**：``session_id`` 一路透到 ``run_task``；续不上**照实说**；
   派完拿到会话号 ⇒ ``_owners`` 里写的是名单，台账那一行也带着名单。

全部用 tmp 里的假家目录与打桩的名册，不碰这台机器上真的会话。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from plobi.delegation import external_sessions as es  # noqa: E402

A = "写稿助手"
B = "建模助手"
C = "调研助手"
ELSEWHERE = r"D:\Projects\Sindri"


# --------------------------------------------------------------------------- #
# 假家目录（与 `test_external_sessions.py` 同一套形状，理由见那里的注释）
# --------------------------------------------------------------------------- #

@pytest.fixture
def homes(tmp_path, monkeypatch):
    qoder = tmp_path / "qoder"
    home = tmp_path / "home"
    for directory in (qoder, home):
        directory.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("QODER_CONFIG_DIR", str(qoder))
    monkeypatch.setenv("PLOBI_HOME", str(home))
    # 账本落点直接钉死（conftest 的自动夹具也会改 PLOBI_HOME，谁后跑说不准；
    # 这一档只关心「写进去的读得回来」，所以把 `index_file()` 定到本档的假家目录）。
    monkeypatch.setattr(es, "index_file",
                        lambda: home / "external_sessions" / "index.json")
    # 别家一律钉到**同一个空目录下**的不存在路径 ⇒ 这一档只看得见我们造的那几条 Qoder 会话。
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir(parents=True, exist_ok=True)
    for name in ("codebuddy_root", "marvis_db", "opencode_db", "kiro_root", "copilot_db",
                 "qoder_cn_root", "gemini_root", "kilo_db", "cline_root", "agy_brain_root",
                 "agy_db", "cursor_db"):
        monkeypatch.setattr(es, name, (lambda suffix: (lambda: elsewhere / suffix))(name))
    es._ALL_CACHE.update({"at": 0.0, "rows": []})
    es._META_CACHE.clear()

    return SimpleNamespace(qoder=qoder, home=home)


def qoder_session(root: Path, project: str, session_id: str, *, title: str,
                  updated: str = "2026-10-27T10:00:00.000Z") -> None:
    directory = root / "projects" / es.project_key(project)
    directory.mkdir(parents=True, exist_ok=True)
    records = [
        {"type": "workspace-directories", "sessionId": session_id, "directories": [project]},
        {"type": "user", "message": {"role": "user", "content": title}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "好"},
        ]}},
    ]
    (directory / f"{session_id}.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n", encoding="utf-8")
    (directory / session_id).mkdir(exist_ok=True)
    (directory / session_id / "state.json").write_text(
        json.dumps({"sessionId": session_id, "updatedAt": updated}), encoding="utf-8")


def stub_roster(monkeypatch, *names: str) -> None:
    """名册上就这几个人（其余一概「名册里没有」）。"""
    agents = {
        name: SimpleNamespace(name=name, display_name="", project_path=ELSEWHERE,
                              project_paths=(ELSEWHERE,))
        for name in names
    }

    class _Registry:
        @classmethod
        def load(cls):
            return SimpleNamespace(agents=agents)

    import plobi.agents.registry as registry_mod

    monkeypatch.setattr(registry_mod, "AgentRegistry", _Registry)


def rows_for(agent_name: str) -> dict[str, dict]:
    return {f"{item['vendor']}:{item['id']}": item
            for item in es.sessions_for_agent(agent_name)["sessions"]}


def index_payload(homes) -> dict:
    path = homes.home / "external_sessions" / "index.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# 一、多归属：一条会话可以有两个主人
# --------------------------------------------------------------------------- #

def test_a_session_can_belong_to_two_agents_at_once(homes, monkeypatch):
    """判给 `[A, B]` ⇒ **两个人都拿 True**，不在名单里的 C 拿 False。"""
    qoder_session(homes.qoder, r"D:\Temp\gbk-notes", "aaaaaaaa-1111", title="一堆看不懂的笔记")
    stub_roster(monkeypatch, A, B, C)

    out = es.assign_owner([{"vendor": "qoder", "id": "aaaaaaaa-1111",
                            "owners": [A, B], "purpose": "资料调研"}], by="l1")
    assert out["ok"] is True, out
    assert out["written"][0]["owners"] == [A, B]

    for name in (A, B):
        row = rows_for(name)["qoder:aaaaaaaa-1111"]
        assert row["related"] is True, name
        assert row["related_source"] == "owner"
        assert row["owners"] == [A, B]

    other = rows_for(C)["qoder:aaaaaaaa-1111"]
    assert other["related"] is False
    assert other["related_source"] == "owner"


def test_one_name_in_the_list_means_only_that_one_sees_it(homes, monkeypatch):
    """只写一个人 ⇒ 那个人 True，另一个（名册上有、名单上没有）False。"""
    qoder_session(homes.qoder, r"D:\Temp\gbk-notes", "aaaaaaaa-1111", title="笔记")
    stub_roster(monkeypatch, A, B)
    es.assign_owner([{"vendor": "qoder", "id": "aaaaaaaa-1111", "owners": [A]}], by="l1")

    assert rows_for(A)["qoder:aaaaaaaa-1111"]["related"] is True
    assert rows_for(A)["qoder:aaaaaaaa-1111"]["owners"] == [A]
    assert rows_for(B)["qoder:aaaaaaaa-1111"]["related"] is False


def test_the_owner_record_lands_in_the_ledger_as_a_list(homes, monkeypatch):
    """落盘形状就按 §2.1：顶层保留段 `_owners`，键是 `<vendor>:<id>`，格名是 `owners`（数组）。"""
    qoder_session(homes.qoder, r"D:\Temp\gbk-notes", "aaaaaaaa-1111", title="笔记")
    stub_roster(monkeypatch, A, B)
    es.assign_owner([{"vendor": "qoder", "id": "aaaaaaaa-1111", "owners": [A, B],
                      "title": "[写稿助手] 看笔记", "purpose": "资料调研"}], by=A)

    record = index_payload(homes)["_owners"]["qoder:aaaaaaaa-1111"]
    assert record["owners"] == [A, B]
    assert "owner" not in record          # 单值形是早期的形状：改成就只留名单，不留两份真源
    assert record["purpose"] == "资料调研"
    assert record["title"] == "[写稿助手] 看笔记"
    assert record["by"] == A
    assert record["at"]


# --------------------------------------------------------------------------- #
# 二、四种语义分得开：没记录 / 空名单 / 一个人 / 两个人
# --------------------------------------------------------------------------- #

def test_no_record_means_the_old_chain_still_decides(homes, monkeypatch):
    """**没记录** ⇒ 老那条链一个字不改：标题里带着 Agent 名字的，照旧命中 `title`。"""
    qoder_session(homes.qoder, r"D:\Temp", "bbbbbbbb-2222", title=f"给 {A} 写个脚本")
    stub_roster(monkeypatch, A)

    row = rows_for(A)["qoder:bbbbbbbb-2222"]
    assert row["related"] is True
    assert row["related_source"] == "title"
    assert "owners" not in row         # 没判过的会话不长这两格（形状与今天一致）


def test_an_empty_list_means_judged_but_ownerless(homes, monkeypatch):
    """**`owners=[]`** ⇒ 判过了、无主 ⇒ 谁拿都是 `None`（与「没记录」是两句话）。"""
    qoder_session(homes.qoder, r"D:\Temp", "bbbbbbbb-2222", title=f"给 {A} 写个脚本")
    stub_roster(monkeypatch, A, B)
    # 老链本来会按标题判成 True —— 清空名单之后必须压回 None，证明名单那一段真的说了算。
    out = es.assign_owner([{"vendor": "qoder", "id": "bbbbbbbb-2222", "owners": []}], by="l1")
    assert out["ok"] is True, out
    assert out["written"][0]["note"] == "已清空归属（回到待判）"

    for name in (A, B):
        row = rows_for(name)["qoder:bbbbbbbb-2222"]
        assert row["related"] is None, name
        assert row["related_source"] == "owner"
        assert row["owners"] == []


def test_a_verdict_overrides_the_title_heuristic(homes, monkeypatch):
    """判过的**说了算**：标题像 B 的活，判给 `[A]` ⇒ A 拿 True，B 拿 False。"""
    qoder_session(homes.qoder, r"D:\Temp", "cccccccc-3333", title=f"{B} 的建模仿真")
    stub_roster(monkeypatch, A, B)
    es.assign_owner([{"vendor": "qoder", "id": "cccccccc-3333", "owners": [A]}], by="l1")

    assert rows_for(A)["qoder:cccccccc-3333"]["related"] is True
    assert rows_for(B)["qoder:cccccccc-3333"]["related"] is False


# --------------------------------------------------------------------------- #
# 三、兼容早期单值形（账本里的 `owner="A"` / `owner=""`）
# --------------------------------------------------------------------------- #

def test_an_old_string_owner_record_reads_as_a_single_name_list(homes, monkeypatch):
    """老账本里是 `owner="A"` ⇒ 当 `[A]` 读（A True、B False），**不迁移文件**。"""
    qoder_session(homes.qoder, r"D:\Temp", "aaaaaaaa-1111", title="笔记")
    stub_roster(monkeypatch, A, B)
    path = homes.home / "external_sessions" / "index.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"_owners": {
        "qoder:aaaaaaaa-1111": {"owner": A, "purpose": "资料调研", "by": "l1"},
    }}, ensure_ascii=False), encoding="utf-8")

    assert rows_for(A)["qoder:aaaaaaaa-1111"]["related"] is True
    assert rows_for(A)["qoder:aaaaaaaa-1111"]["owners"] == [A]
    assert rows_for(B)["qoder:aaaaaaaa-1111"]["related"] is False
    # 读侧不该回头改文件
    assert index_payload(homes)["_owners"]["qoder:aaaaaaaa-1111"] == {
        "owner": A, "purpose": "资料调研", "by": "l1"}


def test_an_old_empty_string_owner_reads_as_an_empty_list(homes, monkeypatch):
    """老账本里是 `owner=""` ⇒ 当 `[]` 读（判过了但无主 ⇒ None）。"""
    qoder_session(homes.qoder, r"D:\Temp", "bbbbbbbb-2222", title=f"给 {A} 写个脚本")
    stub_roster(monkeypatch, A)
    path = homes.home / "external_sessions" / "index.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"_owners": {"qoder:bbbbbbbb-2222": {"owner": ""}}}),
                    encoding="utf-8")

    row = rows_for(A)["qoder:bbbbbbbb-2222"]
    assert row["related"] is None
    assert row["related_source"] == "owner"
    assert row["owners"] == []


def test_a_bare_string_owner_argument_is_wrapped_into_a_list(homes, monkeypatch):
    """工具参数传字符串（老调用方的手法）⇒ 内部包成 `[那一个人]`，落盘就是名单。"""
    qoder_session(homes.qoder, r"D:\Temp", "aaaaaaaa-1111", title="笔记")
    stub_roster(monkeypatch, A)

    out = es.assign_owner([{"vendor": "qoder", "id": "aaaaaaaa-1111", "owner": A}], by="l1")

    assert out["ok"] is True, out
    assert out["written"][0]["owners"] == [A]
    assert index_payload(homes)["_owners"]["qoder:aaaaaaaa-1111"]["owners"] == [A]


def test_normalize_owners_reads_both_shapes_the_same_way():
    """一个口径：数组去空去重保序；字符串当一个人；空串／空值当空名单。"""
    assert es.normalize_owners([A, "", B, A]) == [A, B]
    assert es.normalize_owners(A) == [A]
    assert es.normalize_owners("") == []
    assert es.normalize_owners(None) == []
    # 老记录（单值 owner）与新记录（名单）读出来是同一个形状
    assert es.record_owners({"owner": A}) == [A]
    assert es.record_owners({"owner": ""}) == []
    assert es.record_owners({"owners": [A, B]}) == [A, B]
    assert es.record_owners({"owners": [], "owner": A}) == []      # 名单在场就以名单为准


# --------------------------------------------------------------------------- #
# 四、名单里的人查不到：整条拒掉 / 逐个剔掉
# --------------------------------------------------------------------------- #

def test_an_unknown_name_in_the_list_is_refused_and_nothing_is_written(homes, monkeypatch):
    qoder_session(homes.qoder, r"D:\Temp", "dddddddd-4444", title="笔记")
    stub_roster(monkeypatch, A)

    out = es.assign_owner([{"vendor": "qoder", "id": "dddddddd-4444",
                            "owners": ["查无此人"]}], by="l1")

    assert out["ok"] is False
    assert out["written"] == []
    assert "名册里没有这个人" in out["rejected"][0]["reason"]
    assert "查无此人" in out["rejected"][0]["reason"]
    assert "_owners" not in index_payload(homes)      # 一个字节都不许落


def test_a_half_known_list_is_refused_whole_not_half_written(homes, monkeypatch):
    """名单里有一个人查不到 ⇒ **整条**不写（不许落半条脏数据），并点名是哪一个。"""
    qoder_session(homes.qoder, r"D:\Temp", "dddddddd-4444", title="笔记")
    stub_roster(monkeypatch, A)

    out = es.assign_owner([{"vendor": "qoder", "id": "dddddddd-4444",
                            "owners": [A, "查无此人"]}], by="l1")

    assert out["ok"] is False
    assert out["written"] == []
    reason = out["rejected"][0]["reason"]
    assert "查无此人" in reason and A not in reason.split("（")[0].split("：")[-1]
    assert "_owners" not in index_payload(homes)


def test_a_name_that_left_the_roster_is_dropped_one_by_one(homes, monkeypatch):
    """名单 `[A, B]` 里 A 走了 ⇒ 剔掉 A，剩下的 B 照旧按名单判（B True、C False）。"""
    qoder_session(homes.qoder, r"D:\Temp", "eeeeeeee-5555", title="笔记")
    stub_roster(monkeypatch, A, B, C)
    es.assign_owner([{"vendor": "qoder", "id": "eeeeeeee-5555", "owners": [A, B]}], by="l1")

    stub_roster(monkeypatch, B, C)      # A 改名／删了
    assert rows_for(B)["qoder:eeeeeeee-5555"]["related"] is True
    assert rows_for(C)["qoder:eeeeeeee-5555"]["related"] is False


def test_everyone_gone_means_ownerless_with_a_note(homes, monkeypatch):
    """名单**全**剔光 ⇒ `None` ＋ 人话（`owner-gone`），**不许**当 False 把会话藏起来。"""
    qoder_session(homes.qoder, r"D:\Temp", "eeeeeeee-5555", title="笔记")
    stub_roster(monkeypatch, A, B)
    es.assign_owner([{"vendor": "qoder", "id": "eeeeeeee-5555", "owners": [A, B]}], by="l1")

    stub_roster(monkeypatch, C)        # A、B 都没了，换 C 来看这一条
    row = rows_for(C)["qoder:eeeeeeee-5555"]

    assert row["related"] is None
    assert row["related_source"] == "owner-gone"
    assert A in row["owner_note"] and B in row["owner_note"]


# --------------------------------------------------------------------------- #
# 五、派活：续接透传 / 照实说 / 派完记名（名单版）
# --------------------------------------------------------------------------- #

@pytest.fixture
def run_env(homes, monkeypatch, tmp_path):
    """把 `_handle_run` 外面的真东西全换成桩：名册、可用性、通道、台账。"""
    from tools import offload_tools as tools_mod

    seen: dict = {}
    monkeypatch.setattr(tools_mod, "agent_roster", lambda: [
        SimpleNamespace(name="qoder", can_use=True, why_not=""),
    ])
    monkeypatch.setattr(tools_mod, "availability", lambda agent: (True, ""))

    captured_ledger: list = []

    class _Ledger:
        def append(self, row, brief=""):
            captured_ledger.append(row)
            seen["ledger_row"] = row

    monkeypatch.setattr(tools_mod, "RoundLedger", _Ledger)

    class _Tickets:
        def open_ticket(self, **kwargs):
            return SimpleNamespace(id="tkt_test")

    import plobi.delegation.tickets as tickets_mod

    monkeypatch.setattr(tickets_mod, "TicketLedger", _Tickets)

    import gateway.session_context as ctx_mod

    monkeypatch.setattr(ctx_mod, "get_session_env", lambda key: "")

    from agent import external_offload as channel

    def fake_run_task(task, **kwargs):
        seen["run_task"] = {"task": task, **kwargs}
        session = kwargs.get("session_id") or ""
        owners = list(kwargs.get("owners") or ())
        return channel.OffloadResult(
            agent="qoder", cwd=str(tmp_path), permission_flags=(), sandboxed=False,
            exit_code=0, duration_s=1.0, argv=("qodercli",), usage={}, cost=None,
            cost_status="reported", artifacts=(), text="干完了", ok=True, error="",
            external_session=session or "new-session-0001",
            external_session_source="acp.load" if session else "acp.sessionId",
            leg="acp", owners=owners, owner="、".join(owners),
            title=kwargs.get("title", ""), purpose=kwargs.get("purpose", ""),
            resumed=bool(session),
        )

    monkeypatch.setattr(channel, "run_task", fake_run_task)

    return SimpleNamespace(seen=seen, ledger=captured_ledger, home=homes.home)


def test_the_session_id_reaches_run_task_and_the_call_is_resumed(run_env):
    """**续接真的透下去**：工具收到的 `session_id` 到 `run_task` 那层还在。"""
    from tools.offload_tools import _handle_run

    out = json.loads(_handle_run({
        "task": "接着说", "agent": "qoder", "session_id": "aaaaaaaa-1111",
        "owners": [A], "title": f"[{A}] 接着说", "purpose": "代码改动",
    }))

    assert run_env.seen["run_task"]["session_id"] == "aaaaaaaa-1111"
    assert run_env.seen["run_task"]["owners"] == [A]
    assert run_env.seen["run_task"]["purpose"] == "代码改动"
    assert out["resumed"] is True
    assert out["external_session"] == "aaaaaaaa-1111"
    assert "resume_note" not in out


def test_a_leg_that_cannot_resume_says_so_instead_of_pretending(run_env, monkeypatch):
    """续不上 ⇒ 返回里有「没续上、是新开的」这句，**不许**静默新建。"""
    from agent import external_offload as channel
    from tools.offload_tools import _handle_run

    def cli_only(task, **kwargs):
        return channel.OffloadResult(
            agent="qoder", cwd="", permission_flags=(), sandboxed=False, exit_code=0,
            duration_s=1.0, argv=("qodercli",), usage={}, cost=None, cost_status="reported",
            artifacts=(), text="干完了", ok=True, error="",
            external_session="brand-new-9999", external_session_source="cli", leg="cli",
            owners=list(kwargs.get("owners") or ()), resumed=False,
        )

    monkeypatch.setattr(channel, "run_task", cli_only)
    out = json.loads(_handle_run({"task": "接着说", "agent": "qoder",
                                  "session_id": "aaaaaaaa-1111"}))

    assert out["resumed"] is False
    assert "没能续上" in out["resume_note"]
    assert "brand-new-9999" in out["resume_note"]


def test_dispatch_records_the_whole_list_right_after_the_session_id_is_known(run_env, monkeypatch):
    """**记名时机**：派完拿到 `external_session` ⇒ `_owners` 里是**名单**，台账那行也带着它。"""
    from tools.offload_tools import _handle_run

    stub_roster(monkeypatch, A, B, C)

    out = json.loads(_handle_run({
        "task": "接着说", "agent": "qoder", "session_id": "aaaaaaaa-1111",
        "owners": [A, B], "title": "[写稿助手] 接着说", "purpose": "资料调研",
    }))

    assert out["ok"] is True
    assert out["owners"] == [A, B]
    record = es.owners_snapshot()["qoder:aaaaaaaa-1111"]
    assert record["owners"] == [A, B]
    assert record["purpose"] == "资料调研"
    row = run_env.seen["ledger_row"]
    assert row.owners == [A, B]
    assert (row.title, row.purpose, row.resumed) == ("[写稿助手] 接着说", "资料调研", True)


def test_dispatch_takes_a_bare_string_owner_for_old_callers(run_env, monkeypatch):
    """老调用方传 `owner="A"` ⇒ 当 `[A]` 用（兼容，不改老用法）。"""
    from tools.offload_tools import _handle_run

    stub_roster(monkeypatch, A)

    out = json.loads(_handle_run({"task": "接着说", "agent": "qoder",
                                  "owner": A, "session_id": "aaaaaaaa-1111"}))

    assert out["owners"] == [A]
    assert es.owners_snapshot()["qoder:aaaaaaaa-1111"]["owners"] == [A]


def test_dispatch_without_any_ownership_words_writes_no_record(run_env):
    """没提 owners/title/purpose ⇒ **一条都不写**（别把「没提」写成「判过了＝无主」）。"""
    from tools.offload_tools import _handle_run

    _handle_run({"task": "随口一问", "agent": "qoder"})

    assert es.owners_snapshot() == {}


def test_the_channel_carries_the_list_and_keeps_the_legacy_cell(monkeypatch):
    """通道那层：`owners` 传字符串也认，结果里既留名单、也留老读侧认的逗号串。"""
    from agent import external_offload as channel

    def stub_legs(task, **kwargs):
        return channel.OffloadResult(
            agent="qoder", cwd="", permission_flags=(), sandboxed=False, exit_code=0,
            duration_s=1.0, argv=("qodercli",), usage={}, cost=None, cost_status="reported",
            artifacts=(), text="干完了", ok=True, error="",
            external_session="aaaaaaaa-1111", leg="cli")

    monkeypatch.setattr(channel, "_run_task_legs", stub_legs)

    assert channel.run_task("干活", owners=A).owners == [A]
    assert channel.run_task("干活", owners=[A, B, ""]).owners == [A, B]
    assert channel.run_task("干活", owners="").owners == []
    assert channel.run_task("干活", owners=[A, B]).owner == "、".join([A, B])


# --------------------------------------------------------------------------- #
# 六、通览那一屏（给 L1 判归属用）
# --------------------------------------------------------------------------- #

def test_scan_all_shows_every_session_with_its_list(homes, monkeypatch):
    from tools.offload_tools import _handle_scan

    qoder_session(homes.qoder, r"D:\Temp", "aaaaaaaa-1111", title="笔记")
    qoder_session(homes.qoder, r"D:\Temp", "bbbbbbbb-2222", title="另一条")
    stub_roster(monkeypatch, A, B)
    es.assign_owner([{"vendor": "qoder", "id": "aaaaaaaa-1111", "owners": [A, B],
                      "purpose": "资料调研"}], by="l1")

    out = json.loads(_handle_scan({"scope": "all"}))

    assert out["ok"] is True
    assert out["scope"] == "all"
    rows = {row["id"]: row for row in out["sessions"]}
    assert rows["aaaaaaaa-1111"]["owners"] == [A, B]
    assert rows["aaaaaaaa-1111"]["purpose"] == "资料调研"
    assert rows["aaaaaaaa-1111"]["related"] is None      # L1 这一行没有「我」，只报归谁
    assert rows["bbbbbbbb-2222"]["owners"] == []


def test_scan_all_works_without_an_agent_identity(homes, monkeypatch):
    """L1 这一行本来就没有 Agent 名 ⇒ `scope="all"` **不许**因此被拦（§2.4）。"""
    from tools.offload_tools import _handle_scan

    qoder_session(homes.qoder, r"D:\Temp", "aaaaaaaa-1111", title="笔记")
    monkeypatch.setattr("tools.offload_tools._agent_identity", lambda: "")

    out = json.loads(_handle_scan({"scope": "all"}))
    assert out["ok"] is True
    assert [row["id"] for row in out["sessions"]] == ["aaaaaaaa-1111"]


# --------------------------------------------------------------------------- #
# 七、向后兼容：没有 `_owners` 的旧账本
# --------------------------------------------------------------------------- #

def test_an_old_ledger_without_the_owners_section_still_reads(homes, monkeypatch):
    qoder_session(homes.qoder, r"D:\Temp", "bbbbbbbb-2222", title=f"给 {A} 写个脚本")
    stub_roster(monkeypatch, A)
    path = homes.home / "external_sessions" / "index.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({A: {"qoder:bbbbbbbb-2222": {"pinned": True}}}), encoding="utf-8")

    row = rows_for(A)["qoder:bbbbbbbb-2222"]

    assert row["pinned"] is True                 # 老那一格照旧读得出来
    assert row["related"] is True                # 老那条判链照旧生效
    assert row["related_source"] == "title"
    assert es.owners_snapshot() == {}


# --------------------------------------------------------------------------- #
# 八、工具面：两个 schema 都是名单形状、坏输入逐条回报
# --------------------------------------------------------------------------- #

def test_the_assign_tool_is_registered_with_a_list_of_owners():
    from tools.offload_tools import ASSIGN_SCHEMA

    description = ASSIGN_SCHEMA["description"]
    item = ASSIGN_SCHEMA["parameters"]["properties"]["items"]["items"]
    assert ASSIGN_SCHEMA["name"] == "external_session_assign"
    assert ASSIGN_SCHEMA["parameters"]["required"] == ["items"]
    assert item["required"] == ["vendor", "id"]
    assert item["properties"]["owners"]["type"] == "array"     # 名单，不是单值
    assert "owner" not in item["properties"]
    assert "多归属" in description
    # 软约束只写在描述里（用户明确说不是受控词表）
    assert "软约束" in description
    assert "资料调研" in description
    assert "[<你的名字>] <这件事>" in description


def test_the_run_tool_takes_a_list_of_owners():
    from tools.offload_tools import RUN_SCHEMA

    properties = RUN_SCHEMA["parameters"]["properties"]
    assert properties["owners"]["type"] == "array"
    assert "owner" not in properties


def test_the_assign_handler_rejects_bad_items_one_by_one():
    from tools.offload_tools import _handle_assign

    out = json.loads(_handle_assign({"items": [
        {"vendor": "没有这家", "id": "aaaaaaaa-1111", "owners": [A]},
        {"vendor": "qoder", "id": "不是会话号!", "owners": [A]},
    ]}))

    assert out["ok"] is False
    assert len(out["rejected"]) == 2
    assert "不认识这家软件" in out["rejected"][0]["reason"]
    assert "会话号" in out["rejected"][1]["reason"]
