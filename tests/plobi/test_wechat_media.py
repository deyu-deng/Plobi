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

from plobi.agenda.service import AgendaService
from plobi.collectors.chatlog import media as M
from plobi.collectors.chatlog.client import ChatMessage
from plobi.collectors.chatlog.client import ChatlogUnavailable
from plobi.collectors.chatlog.client import MediaNotFound
from plobi.collectors.chatlog.client import media_of as media_of
from plobi.collectors.chatlog.client import normalize_message
from plobi.collectors.chatlog.config import CollectorConfig
from plobi.collectors.chatlog.confirm import HeuristicConfirmer
from plobi.collectors.chatlog.pipeline import ChatlogPipeline, IngestReport
from plobi.collectors.chatlog.state import SeenStore, TalkerStore
from plobi.pool import store

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
        # 落点接上之后（`TestTheLandingIntoPoolRows`），只有图没有字的一条**不再算空**：
        # 它在池里有标题和 `context.media`，读得出它是什么。以前这一格钉的是"媒体那条腿没接上，
        # 不许放行进链"——那句话连同它的前提一起过期了。
        assert message.is_empty is False


class FakeSidecar:
    def __init__(self, payloads: dict[str, tuple[bytes, str]], *, fail: tuple[str, ...] = (),
                 absent: tuple[str, ...] = ()) -> None:
        self.payloads = payloads
        self.fail = set(fail)
        self.absent = set(absent)
        self.asked: list[tuple[str, str]] = []

    def media_bytes(self, kind: str, key: str):
        self.asked.append((kind, key))
        if key in self.fail:
            raise ChatlogUnavailable("chatlog request failed: HTTP Error 500")
        if key in self.absent:
            raise MediaNotFound("chatlog has no such media")
        return self.payloads.get(key, (b"", ""))


class TestStagingIntoPlobisOwnHome:
    MD5 = "988e2154899728094f58b0ffd516c1c3"

    def items(self, key: str = MD5, kind: str = "image") -> list[dict]:
        return [{"kind": kind, "key": key}]

    def test_the_bytes_land_with_the_suffix_the_engine_said(self, tmp_path) -> None:
        sidecar = FakeSidecar({self.MD5: (b"\x89PNG\r\n\x1a\n" + b"z" * 20, "image/png")})

        res = M.stage_media(sidecar, self.items(), into=tmp_path)

        assert (res.malformed, res.failed, res.dropped, res.absent) == (0, 0, 0, 0)
        target = Path(res.saved[0]["saved_to"])
        assert target.parent == tmp_path / "image"
        assert target.name == f"{self.MD5}.png" and target.is_file()
        assert res.saved[0]["reused"] is False

    def test_a_second_round_reuses_the_file_it_already_has(self, tmp_path) -> None:
        sidecar = FakeSidecar({self.MD5: (b"payload" * 3, "image/jpeg")})
        M.stage_media(sidecar, self.items(), into=tmp_path)

        res = M.stage_media(sidecar, self.items(), into=tmp_path)

        assert res.saved[0]["reused"] is True
        assert sidecar.asked == [("image", self.MD5)], "第二圈不该再问一次侧车"

    def test_a_key_that_is_not_a_key_is_refused_before_it_touches_the_path(self, tmp_path) -> None:
        """消息里带的字符串不许决定文件名：这一格挡的是路径穿越，不是"格式不好看"。"""
        sidecar = FakeSidecar({})

        res = M.stage_media(sidecar, self.items(key="../../../../Windows/win.ini"), into=tmp_path)

        assert (res.saved, res.malformed, res.failed, res.dropped) == ([], 1, 0, 0)
        assert sidecar.asked == []
        assert not (tmp_path.parent / "Windows").exists()

    def test_a_broken_sidecar_is_counted_as_failed_not_as_missing_media(self, tmp_path) -> None:
        """「侧车答不上来」是坏了，要算在 failed 那一格——它和"这件还没进索引"是两件事。"""
        sidecar = FakeSidecar({}, fail=("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",))

        res = M.stage_media(sidecar, self.items(key="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"), into=tmp_path)

        assert (res.saved, res.failed, res.absent) == ([], 1, 0)

    def test_a_404_is_counted_as_absent_so_the_next_round_can_retry_it(self, tmp_path) -> None:
        """侧车回 404 ⇒ 这一件它今天还不知道（索引滞后）：记在 absent 那一格，别混进"坏了"。"""
        sidecar = FakeSidecar({}, absent=(self.MD5,))

        res = M.stage_media(sidecar, self.items(), into=tmp_path)

        assert (res.saved, res.failed, res.absent) == ([], 0, 1)
        assert not (tmp_path / "image").exists()

    def test_an_oversized_item_is_dropped_and_the_number_says_so(self, tmp_path) -> None:
        sidecar = FakeSidecar({self.MD5: (b"x" * 5_000, "image/png")})

        res = M.stage_media(sidecar, self.items(), into=tmp_path, budget=1_000)

        assert res.saved == [] and res.dropped == 1

    def test_the_round_cap_stops_asking_and_reports_what_it_gave_up_on(self, tmp_path) -> None:
        payloads = {f"{i:032x}": (b"x" * 10, "image/png") for i in range(3)}
        sidecar = FakeSidecar(payloads)
        items = [{"kind": "image", "key": k} for k in payloads]

        res = M.stage_media(sidecar, items, into=tmp_path, max_files=1)

        assert len(res.saved) == 1 and res.dropped == 2
        # 到顶之后**一次都不该再问侧车**——否则封顶只保护了磁盘，没保护那次往返。
        assert len(sidecar.asked) == 1

    def test_the_default_home_is_off_the_system_drive(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("PLOBI_WECHAT_MEDIA_DIR", str(tmp_path / "media"))

        assert M.media_dir() == tmp_path / "media"


class TestTheEngineSaysNo:
    """404 与"侧车没了"必须是两种异常：前者是**这一件它还不知道**，下一轮该重试；后者是故障。"""

    @staticmethod
    def _raise(code: int):
        import urllib.error

        def _boom(_request, timeout=None):  # noqa: ANN001
            raise urllib.error.HTTPError("http://127.0.0.1:5030/image/x", code, "nope", {}, None)

        return _boom

    def test_a_404_on_bytes_is_a_media_not_found(self, monkeypatch) -> None:
        from plobi.collectors.chatlog import client as C

        monkeypatch.setattr(C.urllib.request, "urlopen", self._raise(404))

        try:
            C.ChatlogClient("http://127.0.0.1:5030").media_bytes("image", "9" * 32)
        except C.MediaNotFound:
            return
        except C.ChatlogUnavailable as exc:  # 没分开会走这条
            raise AssertionError(f"404 被当成侧车故障了：{exc}") from exc
        raise AssertionError("404 居然算成功")

    def test_a_404_on_info_is_the_same_answer(self, monkeypatch) -> None:
        from plobi.collectors.chatlog import client as C

        monkeypatch.setattr(C.urllib.request, "urlopen", self._raise(404))

        import pytest

        with pytest.raises(C.MediaNotFound):
            C.ChatlogClient("http://127.0.0.1:5030").media_info("image", "9" * 32)

    def test_any_other_http_status_stays_a_broken_sidecar(self, monkeypatch) -> None:
        from plobi.collectors.chatlog import client as C

        monkeypatch.setattr(C.urllib.request, "urlopen", self._raise(503))

        import pytest

        with pytest.raises(C.ChatlogUnavailable) as got:
            C.ChatlogClient("http://127.0.0.1:5030").media_bytes("image", "9" * 32)
        assert not isinstance(got.value, C.MediaNotFound)


class TestTheLandingIntoPoolRows:
    """落点那一笔（`WP-WECHAT-MEDIA` 的第①件）：媒体件跟着那条消息进池。

    钉四件容易跑偏的：取回的件要带**读得回来的路径**；只有图没有字的那条不许落成一行空消息；
    侧车还不知道的那些**不许伪装成"这条没媒体"**（要留得下"还欠几件"、并把这条留给下一轮）；
    一轮的上限到顶之后要**一次都不再问**侧车——封顶保护的是那次往返，不只是磁盘。
    """

    MD5 = "988e2154899728094f58b0ffd516c1c3"

    def item(self, key: str) -> dict:
        return {"kind": "image", "key": key, "key_name": "md5", "path": "", "name": ""}

    def message(self, content: str, *, msg_id: str = "cl_9", media: list[dict] | None = None) -> ChatMessage:
        return ChatMessage(
            msg_id=msg_id, talker="班级群", sender="甲", sent_at="2026-10-30T12:00:00",
            content=content, media=tuple(media if media is not None else [self.item(self.MD5)]),
        )

    def wired(self, tmp_path, client, monkeypatch) -> ChatlogPipeline:
        monkeypatch.setattr(store, "path", lambda: tmp_path / "pool.db")
        monkeypatch.setenv("PLOBI_WECHAT_MEDIA_DIR", str(tmp_path / "media-home"))
        return ChatlogPipeline(
            config=CollectorConfig(mode="whitelist", talkers=["班级群"], enabled=True),
            client=client,
            service=AgendaService(tmp_path / "agenda.db"),
            seen=SeenStore(tmp_path / "seen.db"),
            talkers=TalkerStore(tmp_path / "talkers.db"),
            confirmer=HeuristicConfirmer(),
        )

    def test_a_message_with_an_image_lands_the_file_it_was_asked_for(self, tmp_path, monkeypatch) -> None:
        payload = b"jpeg-bytes" * 4
        pipeline = self.wired(tmp_path, FakeSidecar({self.MD5: (payload, "image/jpeg")}), monkeypatch)
        report = IngestReport()

        pipeline.handle_message(self.message("看下这张图"), report)

        [row] = store.query(source="wechat")
        assert row["body"] == "看下这张图"
        [item] = row["context"]["media"]
        assert item["kind"] == "image" and item["size_bytes"] == len(payload)
        assert Path(item["saved_to"]).is_file()
        assert report.media_saved == 1 and report.media_bytes_staged == len(payload)

    def test_an_image_without_any_text_lands_as_itself_not_as_an_empty_row(self, tmp_path, monkeypatch) -> None:
        pipeline = self.wired(tmp_path, FakeSidecar({self.MD5: (b"x" * 12, "image/png")}), monkeypatch)
        report = IngestReport()

        pipeline.handle_message(self.message(""), report)

        [row] = store.query(source="wechat")
        assert row["title"] == "（图片）"
        assert row["body"] == ""
        assert row["context"]["media"][0]["saved_to"].endswith(".png")

    def test_media_the_sidecar_does_not_know_yet_is_left_for_the_next_round(self, tmp_path, monkeypatch) -> None:
        pipeline = self.wired(tmp_path, FakeSidecar({}, absent=(self.MD5,)), monkeypatch)
        report = IngestReport()

        pipeline.handle_message(self.message("看图"), report)

        [row] = store.query(source="wechat")
        # 「这一件它还不知道」不许写成「这条没媒体」：那一格要说欠几件，行要留着重试的口子。
        assert "media" not in row["context"]
        assert row["context"]["media_pending"] == 1
        assert report.media_absent == 1 and report.media_pending_messages == 1
        assert not pipeline.seen.already_seen("cl_9")

    def test_the_round_cap_stops_asking_the_sidecar_entirely(self, tmp_path, monkeypatch) -> None:
        keys = [f"{n:032x}" for n in range(M.MAX_MEDIA_PER_ROUND + 3)]
        sidecar = FakeSidecar({key: (b"x" * 8, "image/jpeg") for key in keys})
        pipeline = self.wired(tmp_path, sidecar, monkeypatch)
        report = IngestReport()

        for index, key in enumerate(keys):
            pipeline.handle_message(self.message("图", msg_id=f"m{index}", media=[self.item(key)]), report)

        assert len(sidecar.asked) == M.MAX_MEDIA_PER_ROUND
        assert report.media_saved == M.MAX_MEDIA_PER_ROUND
        assert report.media_dropped == len(keys) - M.MAX_MEDIA_PER_ROUND
