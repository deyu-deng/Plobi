"""笔记的改名与删除：后端那一半（WP-NOTE-RENAME-DELETE）。

假的人读面：一个 tmp 目录当 ``HUMAN_ROOT``，底下 ``notes/`` 放几篇笔记。
git 提交那一段不进单测（测试里那个人读面不是仓）⇒ ``committed`` 恒为 False。

测例逐条列，不参数化：每一条钉的是**一件具体的事**，标题写清钉的是什么。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from plobi.mind import note_manage
from plobi.mind import surface_api

EDITED = "# 被人改过了\n"
NOTE_BODY = "---\ntitle: 旧标题\ntags: [工具]\nbelongs: []\n---\n\n# 旧标题\n\n正文第一段。\n第二段。\n"


@pytest.fixture()
def human(tmp_path, monkeypatch):
    """假的人读面：``<tmp>/human`` 当 ``HUMAN_ROOT``，底下 ``notes/工具``。"""
    mind = tmp_path / "mind"
    (mind / "wiki").mkdir(parents=True)
    root = tmp_path / "human"
    (root / "notes" / "工具").mkdir(parents=True)

    monkeypatch.setenv("MIND_ROOT", str(mind))
    monkeypatch.setenv("HUMAN_ROOT", str(root))
    # 注册表那份钉不许进来：它指着这台机器上**真的**人读面。
    from plobi.mind import paths as mind_paths

    monkeypatch.setattr(mind_paths, "_pinned_human_root", lambda: None)
    return root / "notes"


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    return path


def _snapshot(root: Path) -> dict[str, bytes]:
    """盘上每一档的字节（用来钉「一个字节都没动」）。"""
    out: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            out[path.relative_to(root).as_posix()] = path.read_bytes()
    return out


# ── 改名：三处同步（文件名 / frontmatter 的 title / 正文一级标题）──────────────


def test_rename_moves_the_file_and_syncs_the_title_and_the_heading(human):
    """文件名、frontmatter 的 ``title``、正文第一个一级标题，三处一起变成新名字。"""
    source = _write(human / "工具" / "旧名字.md", NOTE_BODY)

    result = note_manage.rename_note("Notes/工具/旧名字.md", "新名字")

    assert result["kind"] == "rename"
    assert result["path"] == "Notes/工具/旧名字.md"
    assert result["new_path"] == "Notes/工具/新名字.md"
    assert not source.exists()
    text = (human / "工具" / "新名字.md").read_bytes().decode("utf-8")
    assert "title: 新名字" in text
    assert "# 新名字\n" in text


def test_rename_leaves_every_other_byte_alone(human):
    """除那两行之外，其余字节**一个不动**（tags／belongs／正文原样写回）。"""
    _write(human / "工具" / "旧名字.md", NOTE_BODY)

    note_manage.rename_note("Notes/工具/旧名字.md", "新名字")

    text = (human / "工具" / "新名字.md").read_bytes().decode("utf-8")
    assert text.startswith(
        "---\ntitle: 新名字\ntags: [工具]\nbelongs: []\n---\n\n# 新名字\n\n"
    )
    assert text.endswith("正文第一段。\n第二段。\n")


def test_rename_does_not_invent_a_frontmatter(human):
    """没有 frontmatter 的那篇：只改一级标题，不替用户造结构。"""
    _write(human / "工具" / "旧名字.md", "# 旧名字\n\n正文。\n")

    note_manage.rename_note("Notes/工具/旧名字.md", "新名字")

    text = (human / "工具" / "新名字.md").read_bytes().decode("utf-8")
    assert text == "# 新名字\n\n正文。\n"
    assert "title:" not in text


def test_rename_keeps_the_note_in_the_same_folder(human):
    """新路径是**同一个目录** ＋ 新名 ＋ ``.md``，不搬家。"""
    _write(human / "工具" / "旧名字.md", NOTE_BODY)

    result = note_manage.rename_note("Notes/工具/旧名字.md", "新名字")

    assert result["new_path"] == "Notes/工具/新名字.md"
    assert (human / "工具" / "新名字.md").is_file()


# ── 改名：双链跟着走 ───────────────────────────────────────────────────────────


def test_rename_follows_the_backlinks(human):
    """指向它的 ``[[双链]]`` 跟着改；别名与小节锚点原样留着。"""
    _write(human / "工具" / "旧名字.md", NOTE_BODY)
    other = _write(
        human / "工具" / "引用.md",
        "见 [[旧标题]]、[[旧标题|别名]]、[[旧标题#小节]] 三处。\n",
    )

    note_manage.rename_note("Notes/工具/旧名字.md", "新名字")

    text = other.read_bytes().decode("utf-8")
    assert "[[新名字]]" in text
    assert "[[新名字|别名]]" in text
    assert "[[新名字#小节]]" in text


def test_rename_follows_the_backlinks_by_file_stem(human):
    """双链按**文件名**（stem）指着它时也算命中。"""
    _write(human / "工具" / "旧名字.md", NOTE_BODY)
    other = _write(human / "工具" / "引用.md", "见 [[旧名字]]。\n")

    note_manage.rename_note("Notes/工具/旧名字.md", "新名字")

    assert "[[新名字]]" in other.read_bytes().decode("utf-8")


def test_rename_leaves_fenced_wikilinks_alone(human):
    """围栏代码块里的 ``[[…]]`` 是示范，不改。"""
    _write(human / "工具" / "旧名字.md", NOTE_BODY)
    other = _write(
        human / "工具" / "引用.md",
        "```markdown\n[[旧标题]]\n```\n\n正文里的 [[旧标题]]\n",
    )

    note_manage.rename_note("Notes/工具/旧名字.md", "新名字")

    text = other.read_bytes().decode("utf-8")
    assert "```markdown\n[[旧标题]]\n```" in text
    assert "正文里的 [[新名字]]" in text


def test_rename_leaves_empty_link_targets_alone(human):
    """``[[#小节]]``（目标为空）不动——它指的是本篇的小节，不是别的笔记。"""
    _write(human / "工具" / "旧名字.md", NOTE_BODY)
    other = _write(human / "工具" / "引用.md", "见 [[#小节]] 与 [[旧标题]]。\n")

    note_manage.rename_note("Notes/工具/旧名字.md", "新名字")

    text = other.read_bytes().decode("utf-8")
    assert "[[#小节]]" in text
    assert "[[新名字]]" in text


def test_rename_reports_the_notes_it_touched(human):
    """``changes`` ＝ 新路径那一篇 ＋ 每一篇有入链的笔记；``shas`` 记着改之前的 sha。"""
    _write(human / "工具" / "旧名字.md", NOTE_BODY)
    _write(human / "工具" / "引用.md", "见 [[旧标题]]。\n")
    _write(human / "工具" / "无关.md", "这里没有双链。\n")

    op = note_manage.plan_rename("Notes/工具/旧名字.md", "新名字")

    assert [change.path for change in op.changes] == [
        "Notes/工具/新名字.md",
        "Notes/工具/引用.md",
    ]
    assert [path for path, _sha in op.shas] == [
        "Notes/工具/旧名字.md",
        "Notes/工具/引用.md",
    ]


# ── 改名：拒绝与不越界 ────────────────────────────────────────────────────────


def test_rename_refuses_an_existing_target_and_touches_nothing(human):
    """目标已存在 ⇒ 抛错，且盘上**一个字节都没动**（不许覆盖）。"""
    _write(human / "工具" / "旧名字.md", NOTE_BODY)
    _write(human / "工具" / "已存在.md", "# 已存在\n\n别覆盖我。\n")
    before = _snapshot(human)

    with pytest.raises(ValueError):
        note_manage.rename_note("Notes/工具/旧名字.md", "已存在")

    assert _snapshot(human) == before


def test_rename_refuses_a_missing_note(human):
    """原路径不在 ⇒ 抛错，消息说清「这篇不在」。"""
    with pytest.raises(ValueError) as excinfo:
        note_manage.plan_rename("Notes/工具/根本没有.md", "新名字")

    assert "does not exist" in str(excinfo.value)


def test_rename_refuses_an_empty_name(human):
    """空名 ⇒ 抛错。"""
    _write(human / "工具" / "旧名字.md", NOTE_BODY)

    with pytest.raises(ValueError):
        note_manage.plan_rename("Notes/工具/旧名字.md", "   ")


def test_rename_refuses_a_name_with_a_path_separator(human):
    """名字里带路径分隔符 ⇒ 抛错（改名不是搬家）。"""
    _write(human / "工具" / "旧名字.md", NOTE_BODY)

    with pytest.raises(ValueError):
        note_manage.plan_rename("Notes/工具/旧名字.md", "别的目录/新名字")

    with pytest.raises(ValueError):
        note_manage.plan_rename("Notes/工具/旧名字.md", "别的目录\\新名字")


def test_rename_refuses_a_name_starting_with_a_dot(human):
    """以 ``.`` 开头的名字 ⇒ 抛错（隐藏档不进知识库）。"""
    _write(human / "工具" / "旧名字.md", NOTE_BODY)

    with pytest.raises(ValueError):
        note_manage.plan_rename("Notes/工具/旧名字.md", ".隐藏")


def test_paths_outside_the_notes_root_are_refused(human):
    """路径越界（``..``）或不是 ``.md`` ⇒ 一律拒，落点不许出 notes/。"""
    _write(human / "工具" / "旧名字.md", NOTE_BODY)
    before = _snapshot(human)

    with pytest.raises(ValueError):
        note_manage.plan_rename("Notes/../../etc/passwd", "新名字")
    with pytest.raises(ValueError):
        note_manage.plan_rename("Notes/../../etc/passwd.md", "新名字")
    with pytest.raises(ValueError):
        note_manage.plan_rename("Notes/工具/旧名字.txt", "新名字")
    with pytest.raises(ValueError):
        note_manage.plan_delete("Notes/../../etc/passwd")

    assert _snapshot(human) == before


def test_planning_does_not_touch_the_disk(human):
    """``plan_*`` 只算不落：算完盘上一个字节都没动。"""
    _write(human / "工具" / "旧名字.md", NOTE_BODY)
    before = _snapshot(human)

    note_manage.plan_rename("Notes/工具/旧名字.md", "新名字")
    note_manage.plan_delete("Notes/工具/旧名字.md")

    assert _snapshot(human) == before


# ── 人先改过 ⇒ 拒 ─────────────────────────────────────────────────────────────


def test_rename_refuses_when_somebody_else_changed_the_note(human):
    """算完计划之后人又改了那一篇 ⇒ 抛 ``NoteConflict``，且不改任何东西。"""
    source = _write(human / "工具" / "旧名字.md", NOTE_BODY)
    op = note_manage.plan_rename("Notes/工具/旧名字.md", "新名字")
    source.write_bytes(EDITED.encode("utf-8"))

    with pytest.raises(note_manage.NoteConflict):
        note_manage.apply_op(op)

    assert source.read_bytes() == EDITED.encode("utf-8")
    assert not (human / "工具" / "新名字.md").exists()


def test_delete_refuses_when_somebody_else_changed_the_note(human):
    """删除也一样：盘上现状与记下的 sha 对不上就拒，不许把它扔进回收站。"""
    source = _write(human / "工具" / "旧名字.md", NOTE_BODY)
    op = note_manage.plan_delete("Notes/工具/旧名字.md")
    source.write_bytes(EDITED.encode("utf-8"))

    with pytest.raises(note_manage.NoteConflict):
        note_manage.apply_op(op)

    assert source.read_bytes() == EDITED.encode("utf-8")
    assert not (human.parent / ".trash").exists()


def test_a_conflicting_rename_leaves_the_backlinks_alone(human):
    """入链的那一篇被改过 ⇒ 整个改名停手，入链笔记也不许被改。"""
    _write(human / "工具" / "旧名字.md", NOTE_BODY)
    other = _write(human / "工具" / "引用.md", "见 [[旧标题]]。\n")
    op = note_manage.plan_rename("Notes/工具/旧名字.md", "新名字")
    other.write_bytes("见 [[旧标题]]，我刚改过。\n".encode("utf-8"))

    with pytest.raises(note_manage.NoteConflict):
        note_manage.apply_op(op)

    assert "我刚改过" in other.read_bytes().decode("utf-8")
    assert (human / "工具" / "旧名字.md").is_file()


def test_a_rename_op_without_the_new_content_is_refused(human):
    """载荷不全（没有新内容）⇒ 拒：否则改名就等于把那一篇删了。"""
    source = _write(human / "工具" / "旧名字.md", NOTE_BODY)
    op = note_manage.NoteOp(
        kind="rename",
        path="Notes/工具/旧名字.md",
        new_path="Notes/工具/新名字.md",
        new_title="新名字",
        changes=(),
        shas=(("Notes/工具/旧名字.md", note_manage._sha_of(source)),),
    )

    with pytest.raises(ValueError):
        note_manage.apply_op(op)

    assert source.is_file()
    assert not (human / "工具" / "新名字.md").exists()


def test_an_op_without_a_recorded_sha_is_refused(human):
    """载荷里没记 sha ⇒ 拒：比 sha 那道闸不许被绕过。"""
    source = _write(human / "工具" / "旧名字.md", NOTE_BODY)
    op = note_manage.NoteOp(kind="delete", path="Notes/工具/旧名字.md", trash=".trash/x.md")

    with pytest.raises(ValueError):
        note_manage.apply_op(op)

    assert source.is_file()


# ── 删除：移进回收站，不是 unlink ─────────────────────────────────────────────


def test_delete_moves_the_note_into_the_trash(human):
    """删掉的那一篇**还在**回收站里（可恢复），原路径没了。"""
    source = _write(human / "工具" / "旧名字.md", NOTE_BODY)

    result = note_manage.delete_note("Notes/工具/旧名字.md")

    assert not source.exists()
    trashed = human.parent / ".trash" / "Notes" / "工具" / "旧名字.md"
    assert trashed.is_file()
    assert trashed.read_bytes().decode("utf-8") == NOTE_BODY
    assert result["kind"] == "delete"
    assert result["trash"] == ".trash/Notes/工具/旧名字.md"
    assert result["changed"] == ["Notes/工具/旧名字.md"]


def test_delete_keeps_the_old_copy_when_the_trash_already_has_one(human):
    """回收站里已经有同名 ⇒ 加时间戳后缀，不覆盖先前那份。"""
    source = _write(human / "工具" / "旧名字.md", NOTE_BODY)
    earlier = _write(human.parent / ".trash" / "Notes" / "工具" / "旧名字.md", "# 先前那份\n")

    note_manage.delete_note("Notes/工具/旧名字.md")

    assert not source.exists()
    assert earlier.read_bytes().decode("utf-8") == "# 先前那份\n"
    trashed = list((human.parent / ".trash" / "Notes" / "工具").glob("旧名字*.md"))
    assert len(trashed) == 2


# ── 载荷往返（待批队列里存的就是它）──────────────────────────────────────────


def test_a_note_op_survives_the_json_round_trip(human):
    """``to_json``／``from_json`` 要能往返——Agent 那一侧把它存进待批队列。"""
    _write(human / "工具" / "旧名字.md", NOTE_BODY)
    _write(human / "工具" / "引用.md", "见 [[旧标题]]。\n")

    op = note_manage.plan_rename("Notes/工具/旧名字.md", "新名字")
    again = note_manage.NoteOp.from_json(op.to_json())

    assert again == op
    assert json.loads(op.to_json())["kind"] == "rename"


def test_an_unknown_op_kind_is_refused(human):
    """不认识的动作 ⇒ 拒（不许把别的载荷当改名落盘）。"""
    with pytest.raises(ValueError):
        note_manage.NoteOp.from_json(json.dumps({"kind": "format", "path": "Notes/x.md"}))


# ── HTTP 面：同一个信封，同样的状态码 ─────────────────────────────────────────


def _rename(path: str, name: str):
    return asyncio.run(surface_api.rename_surface_note({"path": path, "name": name}))


def _delete(path: str):
    return asyncio.run(surface_api.delete_surface_note({"path": path}))


def _body(response):
    return response if isinstance(response, dict) else json.loads(response.body)


def test_the_rename_endpoint_renames_and_reports(human):
    """200 ＋ ``{ok, data}``；data 里说清新路径与改了哪几档。"""
    _write(human / "工具" / "旧名字.md", NOTE_BODY)
    _write(human / "工具" / "引用.md", "见 [[旧标题]]。\n")

    body = _body(_rename("Notes/工具/旧名字.md", "新名字"))

    assert body["ok"] is True
    assert body["data"]["new_path"] == "Notes/工具/新名字.md"
    assert body["data"]["changed"] == ["Notes/工具/新名字.md", "Notes/工具/引用.md"]
    assert (human / "工具" / "新名字.md").is_file()


def test_the_rename_endpoint_is_404_when_the_note_is_gone(human):
    """那篇不在 ⇒ 404，不是 500。"""
    response = _rename("Notes/工具/根本没有.md", "新名字")

    assert response.status_code == 404
    assert json.loads(response.body)["ok"] is False


def test_the_rename_endpoint_is_400_for_a_bad_name(human):
    """名字不合法 ⇒ 400。"""
    _write(human / "工具" / "旧名字.md", NOTE_BODY)

    response = _rename("Notes/工具/旧名字.md", "别的目录/新名字")

    assert response.status_code == 400


def test_the_rename_endpoint_is_400_for_a_path_outside_notes(human):
    """路径越界 ⇒ 400（不是 404——那是「路径不对」，不是「没有这一篇」）。"""
    response = _rename("Notes/../../etc/passwd", "新名字")

    assert response.status_code == 400


def test_the_rename_endpoint_is_409_when_somebody_else_changed_the_note(human, monkeypatch):
    """人先改过 ⇒ 409（那一刀不许放宽）。"""
    _write(human / "工具" / "旧名字.md", NOTE_BODY)

    def boom(*args, **kwargs):
        raise note_manage.NoteConflict("笔记被人改过了")

    monkeypatch.setattr(note_manage, "rename_note", boom)
    response = _rename("Notes/工具/旧名字.md", "新名字")

    assert response.status_code == 409
    assert json.loads(response.body)["ok"] is False


def test_the_delete_endpoint_moves_the_note_into_the_trash(human):
    """200 ＋ ``data.trash`` 指出回收站里的落点。"""
    _write(human / "工具" / "旧名字.md", NOTE_BODY)

    body = _body(_delete("Notes/工具/旧名字.md"))

    assert body["ok"] is True
    assert body["data"]["trash"] == ".trash/Notes/工具/旧名字.md"
    assert not (human / "工具" / "旧名字.md").exists()
    assert (human.parent / ".trash" / "Notes" / "工具" / "旧名字.md").is_file()


def test_the_delete_endpoint_is_404_when_the_note_is_gone(human):
    """那篇不在 ⇒ 404。"""
    response = _delete("Notes/工具/根本没有.md")

    assert response.status_code == 404


def test_the_delete_endpoint_is_409_when_somebody_else_changed_the_note(human, monkeypatch):
    """人先改过 ⇒ 409。"""
    _write(human / "工具" / "旧名字.md", NOTE_BODY)

    def boom(*args, **kwargs):
        raise note_manage.NoteConflict("笔记被人改过了")

    monkeypatch.setattr(note_manage, "delete_note", boom)
    response = _delete("Notes/工具/旧名字.md")

    assert response.status_code == 409


# ── 收口三处（会话主人 11-10 19:2x 定的）────────────────────────────────────


def test_rename_refuses_a_name_that_carries_the_extension(human):
    """名字带 ``.md`` ⇒ 拒（不然会落成 ``x.md.md``），且盘上一个字节都不动。"""
    _write(human / "工具" / "旧名字.md", NOTE_BODY)
    before = _snapshot(human)

    with pytest.raises(ValueError) as excinfo:
        note_manage.rename_note("Notes/工具/旧名字.md", "新名字.md")

    assert ".md" in str(excinfo.value)
    assert _snapshot(human) == before


def test_rename_keeps_the_comment_at_the_end_of_the_title_line(human):
    """标题行那句 ``# 备注`` 是用户自己写在那儿的话 ⇒ 改名不许把它弄丢。"""
    _write(
        human / "工具" / "旧名字.md",
        "---\ntitle: 旧标题  # 这句别丢\ntags: []\n---\n\n# 旧标题\n\n正文。\n",
    )

    note_manage.rename_note("Notes/工具/旧名字.md", "新名字")

    text = (human / "工具" / "新名字.md").read_bytes().decode("utf-8")
    assert "title: 新名字" in text
    assert "# 这句别丢" in text


def test_rename_leaves_the_links_inside_inline_code_alone(human):
    """行内代码里的 ``[[…]]`` 是示范，不是链接 ⇒ 不许跟着改。

    Obsidian 自己不把行内代码当链接（真库里有 5 处这种示范写法）。
    """
    _write(human / "工具" / "旧名字.md", NOTE_BODY)
    _write(human / "工具" / "别的.md", "正文里指向 [[旧名字]]，行内代码里是 `[[旧名字]]`。\n")

    note_manage.rename_note("Notes/工具/旧名字.md", "新名字")

    text = (human / "工具" / "别的.md").read_bytes().decode("utf-8")
    assert "正文里指向 [[新名字]]" in text
    assert "`[[旧名字]]`" in text
