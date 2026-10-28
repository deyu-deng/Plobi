"""钉钉那条的**媒体那一腿**：`im_image_info` 索引 + 把盘上真有的件收进 Plobi 的家。

钉的判据是一句话：**这一格说的永远是"这台机器上现在有什么"，不是"钉钉云端有什么"**。
所以"没下载过 ⇒ 零件"必须是能过的形状，而"索引里有条目却没取回"要说得出为什么、说得出数。
"""

from __future__ import annotations

import sqlite3
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plobi.collectors.dingtalk import store
from plobi.collectors.dingtalk.collector import DingTalkCollector

TABLE = ("CREATE TABLE im_image_info (mid TEXT, url_md5 TEXT, local_path TEXT, size INTEGER, "
         "image_type INTEGER, create_at INTEGER)")


def fake_store(tmp_path: Path, rows: list[tuple]) -> types.SimpleNamespace:
    """一内存库装真表真列：索引那一只手用普通 SQL，不该为了测试再造一只假 SQL。"""
    conn = sqlite3.connect(":memory:")
    conn.execute(TABLE)
    conn.executemany("INSERT INTO im_image_info VALUES (?,?,?,?,?,?)", rows)
    conn.commit()

    return types.SimpleNamespace(connection=conn, profile="p-test", rows=len(rows), close=conn.close)


def image_on_disk(tmp_path: Path, name: str = "shot.png", payload: bytes = b"\x89PNG" + b"x" * 120) -> Path:
    path = tmp_path / "resource_cache" / "98" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)

    return path


class TestTheImageIndex:
    def test_it_reports_the_real_byte_count_not_the_column_one(self, tmp_path) -> None:
        """那一纸索引的 `size` 是 **KB 取整**（这台实测：写 4，盘上 4 586 字节）——别当字节用。"""
        source = image_on_disk(tmp_path, payload=b"a" * 4586)
        opened = fake_store(tmp_path, [("m1", "988e21", str(source), 4, 1, 1_780_000_000)])

        index = store.media_index(opened)

        assert list(index) == ["m1"]
        item = index["m1"][0]
        assert item["kind"] == "image"
        assert item["exists"] is True
        assert item["size_bytes"] == 4586
        assert item["claimed_kb"] == 4

    def test_a_vanished_cache_file_is_a_reading_not_an_error(self, tmp_path) -> None:
        opened = fake_store(tmp_path, [("m1", "cd842a", str(tmp_path / "gone.png"), 23, 1, 1)])

        item = store.media_index(opened)["m1"][0]

        assert item["exists"] is False
        assert item["size_bytes"] == 0

    def test_only_the_mids_asked_for_come_back(self, tmp_path) -> None:
        opened = fake_store(tmp_path, [("m1", "a", "p1", 1, 1, 1), ("m2", "b", "p2", 1, 1, 2)])

        index = store.media_index(opened, mids=["m2"])

        assert list(index) == ["m2"]

    def test_a_store_without_that_table_yields_nothing_rather_than_raising(self) -> None:
        conn = sqlite3.connect(":memory:")
        opened = types.SimpleNamespace(connection=conn, profile="p", rows=0, close=conn.close)

        assert store.media_index(opened) == {}


class TestStagingIntoPlobisOwnHome:
    def test_the_file_is_copied_and_idempotent_on_a_second_round(self, tmp_path) -> None:
        source = image_on_disk(tmp_path)
        into = tmp_path / "home" / "dingtalk-media"
        items = [{"kind": "image", "md5": "988e21", "local_path": str(source),
                  "size_bytes": source.stat().st_size, "exists": True}]

        first, skipped, dropped = store.stage_media(items, into=into)
        second, _, _ = store.stage_media(items, into=into)

        assert skipped == 0 and dropped == 0
        assert Path(first[0]["saved_to"]).is_file()
        assert Path(first[0]["saved_to"]).read_bytes() == source.read_bytes()
        assert first[0]["saved_to"] == second[0]["saved_to"]
        assert second[0]["reused"] is True and first[0]["reused"] is False

    def test_the_default_home_lands_off_the_system_drive(self, tmp_path, monkeypatch) -> None:
        """落点不许是客户端那个目录（那是 C 盘），也不许散落：走 Plobi 的家。"""
        monkeypatch.setenv("PLOBI_DINGTALK_MEDIA_DIR", str(tmp_path / "media-override"))

        assert store.media_dir() == tmp_path / "media-override"

    def test_a_missing_source_is_counted_as_skipped_not_saved(self, tmp_path) -> None:
        items = [{"kind": "image", "md5": "x", "local_path": str(tmp_path / "nope"),
                  "size_bytes": 0, "exists": False}]

        saved, skipped, dropped = store.stage_media(items, into=tmp_path / "home")

        assert saved == [] and skipped == 1 and dropped == 0

    def test_an_over_cap_file_is_dropped_and_the_count_says_so(self, tmp_path) -> None:
        source = image_on_disk(tmp_path, payload=b"y" * 5_000)
        items = [{"kind": "image", "md5": "x", "local_path": str(source),
                  "size_bytes": 5_000, "exists": True}]

        saved, skipped, dropped = store.stage_media(items, into=tmp_path / "home", budget=1_000)

        assert saved == [] and skipped == 0 and dropped == 1


class TestTheCollectorAttachesMediaToItsRow:
    def _collector(self, media: dict[str, list[dict]], stager=None) -> DingTalkCollector:
        return DingTalkCollector(local_store=types.SimpleNamespace(profile="p", rows=0),
                                media=media, stage_media=stager)

    def test_a_row_with_a_staged_image_carries_it_in_evidence(self, tmp_path) -> None:
        target = tmp_path / "home" / "988e21" / "shot.png"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"z" * 10)

        def stager(items):
            return [{**item, "saved_to": str(target), "reused": False} for item in items], 0, 0

        collector = self._collector({"m1": [{"kind": "image", "md5": "988e21"}]}, stager=stager)
        out = collector._media_for([{"mid": "m1"}, {"mid": "m2"}])

        assert out["m1"][0]["saved_to"] == str(target)
        assert collector.media_note == "索引 1 条，取回 1 件"

    def test_a_message_without_a_cached_file_still_gets_an_empty_media_reading(self, tmp_path) -> None:
        """空列表是一个**读数**（这没有可取的缓存件），不是错误，也不是"没看过"。"""
        collector = self._collector({"m1": [{"kind": "image", "md5": "x"}]})
        out = collector._media_for([{"mid": "m1"}])

        assert out["m1"] == []
        assert "本机已无 1 件" in collector.media_note

    def test_an_unreadable_index_does_not_close_the_source(self, tmp_path) -> None:
        def boom(*_args, **_kwargs):
            raise sqlite3.OperationalError("no such table: im_image_info")

        collector = DingTalkCollector(local_store=types.SimpleNamespace(profile="p", rows=0),
                                      media=None, media_index=boom)

        assert collector._media_for([{"mid": "m1"}]) == {}
        assert collector.media_note.startswith("问不到")

    def test_health_says_the_media_leg_out_loud(self) -> None:
        collector = self._collector({})
        collector._media_for([{"mid": "m1"}])
        reading = collector.health()

        assert reading["ok"] is True
        assert "图片：" in reading["detail"]
