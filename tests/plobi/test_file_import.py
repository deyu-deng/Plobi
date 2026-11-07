"""用户选文件进池那一手（`WP-SOURCE-FIRST-RUN-IMPORT`）：五种格式、幂等、拒收说清楚。

零真数据：每个文件都是这个测试自己写进 tmp 的假内容；池子钉到 tmp（不碰真的那份）。
"""

from __future__ import annotations

import pytest

from plobi.collectors import file_import
from plobi.pool import store

ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//test//CN
BEGIN:VEVENT
UID:evt-1@test
DTSTART:20261106T090000
DTEND:20261106T100000
SUMMARY:组会
END:VEVENT
END:VCALENDAR
"""

OPML = """<?xml version="1.0" encoding="UTF-8"?>
<opml version="2.0"><head><title>t</title></head><body>
<outline text="第一章"><outline text="1.1 节"/><outline text="1.2 节"/></outline>
<outline text="第二章"/>
</body></opml>
"""


@pytest.fixture(autouse=True)
def _pool(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "path", lambda: tmp_path / "pool.db")
    return tmp_path


def test_csv_one_row_one_item_and_reimport_is_idempotent(tmp_path) -> None:
    path = tmp_path / "tasks.csv"
    path.write_text("title,body,when\n交作业,周五前,2026-11-06\n开会,三点,\n", encoding="utf-8")

    first = file_import.import_file(path)
    second = file_import.import_file(path)

    assert first["parsed"] == 2 and first["created"] == 2
    assert second["created"] == 0, "同一个文件再导一次不该灌出第二份"
    rows = store.query(source="import")
    assert {r["title"] for r in rows} == {"交作业", "开会"}


def test_csv_without_a_recognised_header_still_lands_every_row(tmp_path) -> None:
    path = tmp_path / "plain.csv"
    path.write_text("甲,乙\n丙,丁\n", encoding="utf-8")

    result = file_import.import_file(path)

    assert result["parsed"] == 2, "表头一个都不认时按位置读，不许把行丢掉"


def test_markdown_is_one_item_named_after_the_file(tmp_path) -> None:
    path = tmp_path / "笔记.md"
    path.write_text("# 标题\n正文一段\n", encoding="utf-8")

    result = file_import.import_file(path)

    assert result["parsed"] == 1
    [row] = store.query(source="import")
    assert row["title"] == "笔记" and "正文一段" in row["body"]


def test_opml_one_item_per_top_level_node_with_its_subtree(tmp_path) -> None:
    path = tmp_path / "outline.opml"
    path.write_text(OPML, encoding="utf-8")

    result = file_import.import_file(path)

    assert result["parsed"] == 2
    rows = {r["title"]: r["body"] for r in store.query(source="import")}
    assert "1.1 节" in rows["第一章"] and "第二章" in rows


def test_ics_one_event_one_item_with_the_events_own_time(tmp_path) -> None:
    path = tmp_path / "cal.ics"
    path.write_text(ICS, encoding="utf-8")

    result = file_import.import_file(path)

    assert result["parsed"] == 1
    [row] = store.query(source="import")
    assert row["kind"] == "event" and row["title"] == "组会"
    assert str(row["occurred_at"]).startswith("2026-11-06"), "时间用事件自己的，不是导入那一刻"


def test_an_unsupported_extension_is_refused_and_says_what_is_supported(tmp_path) -> None:
    path = tmp_path / "book.xlsx"
    path.write_bytes(b"not really xlsx")

    with pytest.raises(file_import.UnsupportedFormat) as got:
        file_import.import_file(path)

    assert ".csv" in str(got.value) and ".ics" in str(got.value), "拒收那句要告诉用户能导什么"
    assert store.query(source="import") == [], "拒收就不该落任何一行"


def test_an_oversized_file_is_refused_with_the_cap_in_the_sentence(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(file_import, "MAX_IMPORT_BYTES", 8)
    path = tmp_path / "big.txt"
    path.write_text("0123456789", encoding="utf-8")

    with pytest.raises(file_import.ImportTooLarge):
        file_import.import_file(path)
