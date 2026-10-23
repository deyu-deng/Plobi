"""用户 10-23：日程的具体安排（可勾选清单）与附件。

两条判据都钉关系、不钉文案：

1. 清单跟着事件行走：写进去、勾一项、再读回来还是同一批 id；空条目不留。
2. 附件**复制进 Plobi 家目录**再挂上：原文件删了副本还在；摘附件只删副本、不碰原文件；
   删事件时把自己的附件目录一起清掉。超过上限明说拒绝，不偷偷截断。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from plobi.agenda import attachments, store
from plobi.agenda.service import AgendaService


@pytest.fixture()
def home(tmp_path, monkeypatch):
    root = tmp_path / "home"
    root.mkdir()
    monkeypatch.setenv("PLOBI_HOME", str(root))
    return root


@pytest.fixture()
def service(home, tmp_path):
    return AgendaService(tmp_path / "agenda.db")


def _event(service: AgendaService, title: str = "写实验报告"):
    return service.create_manual(title=title, start_at="2026-10-24T09:00:00",
                                 end_at="2026-10-24T11:00:00")


def test_details_round_trip_and_toggle(service):
    event = service.create_manual(
        title="写实验报告",
        start_at="2026-10-24T09:00:00",
        details=[
            {"id": "d1", "text": "带电脑", "done": False},
            {"id": "d2", "text": "查参考资料", "done": True},
            {"text": "   ", "done": False},
        ],
    )

    assert [row["id"] for row in event.details] == ["d1", "d2"]
    assert event.details[1]["done"] is True

    details = [dict(row) for row in event.details]
    details[0]["done"] = True
    updated = service.update_manual(event.id, details=details)

    assert updated.details[0]["done"] is True


def test_attachment_is_copied_into_the_home_and_removable(service, home, tmp_path):
    source = tmp_path / "实验数据.csv"
    source.write_text("a,b\n1,2\n", encoding="utf-8")
    event = _event(service)

    added = service.add_attachment(event.id, str(source))
    assert len(added.attachments) == 1
    row = added.attachments[0]
    copied = Path(row["path"])

    assert copied.is_file() and copied.parent.parent == home / "attachments" / "agenda"
    assert row["name"] == "实验数据.csv" and row["size"] == source.stat().st_size

    source.unlink()  # 原文件删了，副本仍要读得到
    assert copied.is_file()

    removed = service.remove_attachment(event.id, row["id"])
    assert removed.attachments == []
    assert not copied.exists()


def test_oversized_attachment_is_refused_without_writing(service, tmp_path, monkeypatch):
    source = tmp_path / "huge.bin"
    source.write_bytes(b"x" * 32)
    event = _event(service)
    monkeypatch.setattr(attachments, "MAX_ATTACHMENT_BYTES", 8)

    with pytest.raises(store.AgendaValidationError) as exc:
        service.add_attachment(event.id, str(source))

    assert "MB" in str(exc.value) or "小一点" in str(exc.value)
    fresh = service.get(event.id)
    assert fresh.attachments == []
    assert not any((attachments.attachments_root() / event.id).glob("*"))


def test_deleting_the_event_removes_its_attachment_folder(service, tmp_path):
    source = tmp_path / "note.txt"
    source.write_text("hello", encoding="utf-8")
    event = _event(service)
    service.add_attachment(event.id, str(source))
    folder = attachments.attachments_root() / event.id
    assert folder.is_dir()

    service.delete(event.id)

    assert not folder.exists()
    assert source.exists(), "删日程只清自己的副本，原文件不动"
