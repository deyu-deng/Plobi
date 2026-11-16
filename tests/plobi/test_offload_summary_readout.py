"""外包抽屉顶上那一行读数：四个数从台账现读，「今天」按本地那天算。

（WP-OFFLOAD-CONTRACT-UNIFY · 段三·观察面）

**为什么单起一档**：这一格是屏上**唯一**摆「派过几轮／几轮没出错／几轮失败／今天花了多少」
的地方，四个数全是**现读**的 ⇒ 一旦读错，屏上就是一笔假账。三条判据钉住它：

1. 四个数跟**整本台账**对得上（不许拿屏幕上截过的那几行算总数）；
2. 失败摆**人话**（机器码不上屏——它归悬停）；
3. 「今天」按**本地**那天算（台账记的是 UTC），而且「今天没派过活」与
   「今天花了算不出来」是**两件事**，不许混成一句。
"""

from __future__ import annotations

import datetime as _dt

import pytest

from plobi.agents.registry import AgentEntry, AgentRegistry
import plobi.console.router as console_router


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    """一份隔离的名册：读数不许写到这台机器的真家里。"""
    monkeypatch.delenv("PLOBI_PROJECTS_CONFIG", raising=False)
    monkeypatch.setattr("plobi.agents.registry.default_path", lambda: tmp_path / "projects.yaml")
    registry = AgentRegistry(path=tmp_path / "projects.yaml")
    console_router.set_registry(registry)
    yield registry
    console_router.set_registry(None)


def _register(registry, **entries):
    for name, raw in entries.items():
        registry.upsert(AgentEntry.from_dict(name, raw))
    return registry


def _round(cwd: str, ts: str, *, cost=None, failure: str = "") -> dict:
    return {
        "ts": ts,
        "agent": "codebuddy",
        "cwd": cwd,
        "permission_flag": "-y --permission-mode=bypassPermissions",
        "brief": f"推进 {cwd}",
        "artifacts": [],
        "duration_seconds": 16.4,
        "usage": {"input_tokens": 56255} if cost is None else {"cost_usd": cost},
        "cost_status": "usage_reported_without_price",
        "failure": failure,
    }


def _ledger_stub(monkeypatch, rows):
    """把台账换成这几行（台账本身是落盘的 JSONL，测试里不写盘）。"""
    import plobi.delegation.offload as offload_mod

    class _Stub:
        def __init__(self, root=None):
            pass

        def rows(self):
            return list(rows)

    monkeypatch.setattr(offload_mod, "RoundLedger", _Stub)


def _ts(moment: _dt.datetime) -> str:
    """本地那一刻 → 台账里那种 UTC ISO 串（台账记的就是 UTC）。"""
    return moment.astimezone(_dt.timezone.utc).isoformat()


def _local_midnight_today() -> _dt.datetime:
    return _dt.datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)


class TestOffloadSummaryReadout:
    def test_the_four_numbers_come_from_the_whole_ledger_not_the_displayed_page(
        self, _isolated_state, monkeypatch
    ):
        """`limit` 只截**显示**，总数不许跟着变小——屏上那一句按整本台账算。"""
        _register(
            _isolated_state,
            aura={"role": "principal", "profile": "aura", "project_path": "D:/projects/Aura"},
        )
        _ledger_stub(
            monkeypatch,
            [
                _round("D:/projects/Aura", "2026-10-13T01:00:00+00:00", cost=1.0),
                _round("D:/projects/Aura", "2026-10-13T02:00:00+00:00", cost=2.0),
                _round("D:/projects/Aura", "2026-10-13T03:00:00+00:00", cost=3.0, failure="not_logged_in"),
            ],
        )

        out = console_router.agent_offloads("aura", limit=1)

        assert out["total_rounds"] == 3
        assert len(out["rounds"]) == 1, "limit 只截显示的那几行"
        assert "派过 3 轮" in out["summary"]
        assert "2 轮没出错" in out["summary"]
        assert "1 轮失败" in out["summary"]

    def test_a_failed_round_shows_human_words_not_the_machine_code(
        self, _isolated_state, monkeypatch
    ):
        """机器码（`not_logged_in` 那一族）不上屏；后端给的那一句人话原样摆。"""
        _register(
            _isolated_state,
            aura={"role": "principal", "profile": "aura", "project_path": "D:/projects/Aura"},
        )
        _ledger_stub(
            monkeypatch,
            [_round("D:/projects/Aura", "2026-10-13T01:00:00+00:00", failure="not_logged_in")],
        )

        row = console_router.agent_offloads("aura")["rounds"][0]

        assert row["failure_note"], "失败那一格不许是空的"
        assert "not_logged_in" not in row["failure_note"]
        assert "没登录" in row["failure_note"]

    def test_today_is_the_local_calendar_day(self, _isolated_state, monkeypatch):
        """台账记 UTC，屏上写「今天」按**本地**那天算（否则凌晨那几小时会算错天）。"""
        local_now = _dt.datetime.now().astimezone()
        yesterday_ts = _ts(_local_midnight_today() - _dt.timedelta(minutes=1))
        today_ts = _ts(local_now)

        _register(
            _isolated_state,
            aura={"role": "principal", "profile": "aura", "project_path": "D:/projects/Aura"},
        )
        _ledger_stub(
            monkeypatch,
            [
                _round("D:/projects/Aura", yesterday_ts, cost=99.0),
                _round("D:/projects/Aura", today_ts, cost=1.0),
            ],
        )

        summary = console_router.agent_offloads("aura")["summary"]

        assert "今天花了 $1.00" in summary
        assert "$99" not in summary, "昨天那一笔不许算进「今天」"

    def test_nothing_dispatched_today_is_not_said_as_unmeasurable(
        self, _isolated_state, monkeypatch
    ):
        """「今天没派过活」≠「今天花了多少算不出来」：一个是没发生，一个是量不出。"""
        yesterday_ts = _ts(_local_midnight_today() - _dt.timedelta(minutes=1))

        _register(
            _isolated_state,
            aura={"role": "principal", "profile": "aura", "project_path": "D:/projects/Aura"},
        )
        _ledger_stub(monkeypatch, [_round("D:/projects/Aura", yesterday_ts, cost=99.0)])

        summary = console_router.agent_offloads("aura")["summary"]

        assert "今天还没派过活" in summary
        assert "算不出来" not in summary

    def test_a_round_without_a_price_says_it_cannot_be_measured(
        self, _isolated_state, monkeypatch
    ):
        """发生了但量不出 ⇒ 就说量不出（R-073：不许拿 0 充）。"""
        _register(
            _isolated_state,
            aura={"role": "principal", "profile": "aura", "project_path": "D:/projects/Aura"},
        )
        _ledger_stub(
            monkeypatch, [_round("D:/projects/Aura", _ts(_dt.datetime.now().astimezone()), cost=None)]
        )

        summary = console_router.agent_offloads("aura")["summary"]

        assert "今天花了多少算不出来" in summary


def test_the_local_day_helper_follows_the_local_calendar(monkeypatch):
    """那个换算本身：本地那天；读不出来就空串；极端日期不许掀了整格。"""
    ts = _dt.datetime.now(_dt.timezone.utc).isoformat()
    local_day = _dt.datetime.fromisoformat(ts).astimezone().date().isoformat()

    assert console_router._local_day(ts) == local_day
    assert console_router._local_day("") == ""
    assert console_router._local_day("不是时间") == ""
    # 极端日期转本地时区**可能**越界（Windows 抛 OSError ⇒ 空串）；本地时区往回走时
    # 不越界（POSIX UTC ⇒ 读得出那天）。两种都是契约内的读数，
    # 不许的是抛异常、或读出别的日子。
    extreme = console_router._local_day("9999-12-31T23:00:00+00:00")
    assert extreme in ("", "9999-12-31"), extreme

    # 越界那一支单独钉住（哪个平台都成立）：转换一炸就空串，不许掀了整格读数。
    class _OverflowingDatetime(_dt.datetime):
        def astimezone(self, tz=None):
            raise OverflowError("转本地时区越界")

    class _OverflowingModule:
        datetime = _OverflowingDatetime
        timezone = _dt.timezone

    monkeypatch.setattr(console_router, "_dt", _OverflowingModule)
    assert console_router._local_day(ts) == ""
