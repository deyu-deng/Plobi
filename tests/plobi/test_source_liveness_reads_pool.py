"""活性那一只手读的是**池**，不是日程库（10-26 现核出的那处自相矛盾修完的样子）。

档名不叫 `test_pool_*`：那一片 glob 是 `WP-POOL` 的族（`Code/tests/plobi/test_pool*.py`），
这一档测的是"活性读哪份库"，属于信源那一层——归位比排次序干净。

判据不是"多一个函数"，是一句他反复说的话：**同一件事不许有两处答案**。
池里 200 条、日程库里 0 行，屏上却同时出现「已连接」和「从来没有成功采过」——
那是因为"还在不在采"这只手伸错了库。这一档钉四件事：

1. 只进池的那一条，活性必须说"在采"，并且条数就是池里那个数；
2. `events` 那一格仍说**日程库里几条**，两个数各读各的库，谁也不许由谁推；
3. 判活只认"进新条目"那一列（`captured_at`），本地刷 `updated_at` 不算活（模块头那条 10-15 反例在池上同样成立）；
4. 池不在就说读不到，**句子里不出现文件名**（裁定 203 第 1 条）。
"""

from datetime import datetime, timedelta

from plobi.collectors import liveness
from plobi.pool import store as pool_store
from plobi.pool.store import PoolItem, land, mark_acted

NOW = datetime(2026, 10, 26, 12, 0, 0)


def _pool(tmp_path, items) -> object:
    path = tmp_path / "pool.db"
    land(items, db_path=path)
    return path


def _item(source: str, external_id: str, captured: str) -> PoolItem:
    return PoolItem(source=source, external_id=external_id, kind="mail",
                    title="一封邮件", captured_at=captured)


class TestLivenessReadsThePool:
    def test_a_source_that_only_lands_in_the_pool_is_live(self, tmp_path) -> None:
        path = _pool(tmp_path, [_item("email_qq", f"m{i}", "2026-10-26 09:30:16") for i in range(200)])

        row = next(r for r in liveness.read_pool_liveness(
            path, now=NOW, expected_sources=("email_qq",))["sources"] if r["source"] == "email_qq")

        assert row["state"] == liveness.STATE_LIVE
        assert row["events"] == 200
        assert "从来没有成功采过" not in row["note"]

    def test_the_agenda_reader_still_answers_its_own_question(self, tmp_path) -> None:
        """同一份池不能拿去回答"日程里几条"，反过来也不行——这就是那处矛盾的根。"""
        path = _pool(tmp_path, [_item("email_qq", "m1", "2026-10-26 09:30:16")])
        empty_agenda = tmp_path / "agenda.db"
        empty_agenda.write_bytes(b"")          # 一个没有 events 表的文件：日程库里就是 0 行

        agenda = liveness.read_source_liveness(empty_agenda, now=NOW, expected_sources=("email_qq",))
        assert agenda["verdict"].startswith("读不动")   # 读不动那份库就说读不动，不编 0
        assert liveness.read_pool_liveness(path, now=NOW, expected_sources=("email_qq",)
                                           )["sources"][0]["state"] == liveness.STATE_LIVE


class TestTheJudgementIsStillOnlyNewRows:
    def test_touching_updated_at_does_not_make_it_live(self, tmp_path) -> None:
        old = (NOW - timedelta(days=9)).strftime("%Y-%m-%d %H:%M:%S")
        path = _pool(tmp_path, [_item("email_qq", "m1", old)])
        mark_acted("email_qq", "m1", True, db_path=path)     # 人把他处理过一次：刷的是 updated_at

        row = liveness.read_pool_liveness(path, now=NOW, expected_sources=("email_qq",))["sources"][0]

        assert row["state"] == liveness.STATE_STALE, "有人动过板不等于还在采"
        assert row["touched_after_last_write"] is True

    def test_a_source_with_no_row_at_all_says_never(self, tmp_path) -> None:
        path = _pool(tmp_path, [_item("email_qq", "m1", "2026-10-26 09:30:16")])

        row = next(r for r in liveness.read_pool_liveness(
            path, now=NOW, expected_sources=("dingtalk",))["sources"] if r["source"] == "dingtalk")

        assert row["state"] == liveness.STATE_NEVER and row["events"] == 0


class TestWhatTheSentenceMayName:
    def test_a_missing_pool_says_so_without_naming_a_file(self, tmp_path) -> None:
        data = liveness.read_pool_liveness(tmp_path / "nope.db", now=NOW, expected_sources=("email_qq",))

        assert data["sources"][0]["state"] == liveness.STATE_NO_STORE
        assert "那口池" in data["verdict"]
        for text in (data["verdict"], data["sources"][0]["note"]):
            assert ".db" not in text and "pool_items" not in text, "屏幕上那句不许要求他知道文件名"
