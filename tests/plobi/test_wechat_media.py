"""微信这一条的媒体那一腿：`media_of` 认键 + 把侧车解好的字节收进 Plobi 的家。

这里钉的两件事最容易跑偏：
① **键的形状只认本机量到过的**（`contents.md5` / `contents.voice` / 49 里的 `filekey`/`md5`，
   以及引用消息套的那一层）——没量到的写法交回空表，不猜一个键名出来；
② **文件名不许由消息里带的字符串决定**（那等于送一个路径穿越的口），只认 md5 形状。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plobi.collectors.chatlog import media as M
from plobi.collectors.chatlog.client import ChatlogUnavailable
from plobi.collectors.chatlog.client import media_of as media_of
from plobi.collectors.chatlog.client import normalize_message


class TestTheKeysComeFromContents:
    def test_an_image_message_gives_its_md5(self) -> None:
        raw = {"type": 3, "subType": 0, "content": "", "talker": "t@chatroom", "seq": 1,
               "time": "2026-10-27T09:48:53+08:00", "contents": {"md5": "7d47bbc594ec8b71f8cc5bb3a57437ea"}}

        assert media_of(raw) == [{"kind": "image", "key": "7d47bbc594ec8b71f8cc5bb3a57437ea",
                                  "key_name": "md5", "path": "", "name": ""}]

    def test_a_video_carries_both_mds_and_the_relative_path(self) -> None:
        raw = {"type": 43, "contents": {"md5": "2f18c5a28fe369d204fc65754328e396",
                                        "rawmd5": "46ef5ae87c93d1ce63ad5ade1e6607c9",
                                        "path": "msg\\video\\2026-10\\7dc20cf93dc1f180462f5f8e0d5561ea"}}

        one = media_of(raw)[0]

        assert one["kind"] == "video" and one["key"] == "2f18c5a28fe369d204fc65754328e396"
        assert one["path"].startswith("msg")

    def test_a_voice_gives_the_engine_its_own_id_not_an_md5(self) -> None:
        raw = {"type": 34, "contents": {"voice": "475366196732449361"}}

        assert media_of(raw) == [{"kind": "voice", "key": "475366196732449361",
                                  "key_name": "voice", "path": "", "name": ""}]

    def test_a_quoted_image_inside_a_share_is_read_as_quoted(self) -> None:
        raw = {"type": 49, "subType": 57, "contents": {
            "refer": {"type": 3, "contents": {"md5": "aaaa2154899728094f58b0ffd516c1c3"}}}}

        one = media_of(raw)[0]

        assert one["kind"] == "image" and one["from_refer"] is True

    def test_a_link_share_has_no_media_and_says_so_with_an_empty_list(self) -> None:
        """49/5 是链接分享：没有字节可取 ⇒ 空表是一个读数，不是"取失败"。"""
        raw = {"type": 49, "subType": 5, "contents": {"title": "一篇文章", "url": "https://example.test/x"}}

        assert media_of(raw) == []

    def test_an_unrecognised_contents_shape_yields_nothing_instead_of_a_guess(self) -> None:
        assert media_of({"type": 3, "contents": {"cdnbigimgurl": "https://example.test"}}) == []
        assert media_of({"type": 1, "content": "纯文本"}) == []

    def test_the_normaliser_carries_the_media_alongside_the_text(self) -> None:
        message = normalize_message({"type": 3, "talker": "t@chatroom", "seq": 7,
                                     "time": "2026-10-27", "content": "",
                                     "contents": {"md5": "988e2154899728094f58b0ffd516c1c3"}})

        assert message is not None
        assert message.media[0]["key"] == "988e2154899728094f58b0ffd516c1c3"
        # 只有图没有字的一条，在现有那条链上仍然是"不能当一句话读"——媒体那一腿没接进落点之前不许放行。
        assert message.is_empty is True


class FakeSidecar:
    def __init__(self, payloads: dict[str, tuple[bytes, str]], *, fail: tuple[str, ...] = ()) -> None:
        self.payloads = payloads
        self.fail = set(fail)
        self.asked: list[tuple[str, str]] = []

    def media_bytes(self, kind: str, key: str):
        self.asked.append((kind, key))
        if key in self.fail:
            raise ChatlogUnavailable("no such media")
        return self.payloads.get(key, (b"", ""))


class TestStagingIntoPlobisOwnHome:
    MD5 = "988e2154899728094f58b0ffd516c1c3"

    def items(self, key: str = MD5, kind: str = "image") -> list[dict]:
        return [{"kind": kind, "key": key}]

    def test_the_bytes_land_with_the_suffix_the_engine_said(self, tmp_path) -> None:
        sidecar = FakeSidecar({self.MD5: (b"\x89PNG\r\n\x1a\n" + b"z" * 20, "image/png")})

        saved, malformed, failed, dropped = M.stage_media(sidecar, self.items(), into=tmp_path)

        assert (malformed, failed, dropped) == (0, 0, 0)
        target = Path(saved[0]["saved_to"])
        assert target.parent == tmp_path / "image"
        assert target.name == f"{self.MD5}.png" and target.is_file()
        assert saved[0]["reused"] is False

    def test_a_second_round_reuses_the_file_it_already_has(self, tmp_path) -> None:
        sidecar = FakeSidecar({self.MD5: (b"payload" * 3, "image/jpeg")})
        M.stage_media(sidecar, self.items(), into=tmp_path)

        saved, _, _, _ = M.stage_media(sidecar, self.items(), into=tmp_path)

        assert saved[0]["reused"] is True
        assert sidecar.asked == [("image", self.MD5)], "第二圈不该再问一次侧车"

    def test_a_key_that_is_not_a_key_is_refused_before_it_touches_the_path(self, tmp_path) -> None:
        """消息里带的字符串不许决定文件名：这一格挡的是路径穿越，不是"格式不好看"。"""
        sidecar = FakeSidecar({})

        saved, malformed, failed, dropped = M.stage_media(
            sidecar, self.items(key="../../../../Windows/win.ini"), into=tmp_path)

        assert (saved, malformed, failed, dropped) == ([], 1, 0, 0)
        assert sidecar.asked == []
        assert not (tmp_path.parent / "Windows").exists()

    def test_a_sidecar_answer_of_none_is_counted_as_failed_not_missing_media(self, tmp_path) -> None:
        sidecar = FakeSidecar({}, fail=("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",))

        saved, malformed, failed, dropped = M.stage_media(
            sidecar, self.items(key="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"), into=tmp_path)

        assert (saved, malformed, failed, dropped) == ([], 0, 1, 0)

    def test_an_oversized_item_is_dropped_and_the_number_says_so(self, tmp_path) -> None:
        sidecar = FakeSidecar({self.MD5: (b"x" * 5_000, "image/png")})

        saved, _, _, dropped = M.stage_media(sidecar, self.items(), into=tmp_path, budget=1_000)

        assert saved == [] and dropped == 1

    def test_the_round_cap_stops_asking_and_reports_what_it_gave_up_on(self, tmp_path) -> None:
        payloads = {f"{i:032x}": (b"x" * 10, "image/png") for i in range(3)}
        sidecar = FakeSidecar(payloads)
        items = [{"kind": "image", "key": k} for k in payloads]

        saved, _, _, dropped = M.stage_media(sidecar, items, into=tmp_path, max_files=1)

        assert len(saved) == 1 and dropped == 2
        # 到顶之后**一次都不该再问侧车**——否则封顶只保护了磁盘，没保护那次往返。
        assert len(sidecar.asked) == 1

    def test_the_default_home_is_off_the_system_drive(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("PLOBI_WECHAT_MEDIA_DIR", str(tmp_path / "media"))

        assert M.media_dir() == tmp_path / "media"
