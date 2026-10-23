"""H1 接口面当场复跑核（裁定 139④(a) / 140；契约真源是 `Plobi-Harmony-App/README.md` §公开 API）。

移动端整段按裁定 139 押后，**但 H1「连得上」那一半不撤**：设备面前缀 + 每台一张可单独撤销的配对
令牌，就是「预留的接口」本身。预留要成立，前提是这份接口的**形状能被命令跑出来**，而不是写在两
份文档里互相看着像。本文件把后端今天真实吐出的形状钉成断言：

- 路径面：五条读口 + 两条写口（`confirm` / `dismiss`）+ 配对兑换口；设备令牌开不了浏览器面；
- 载荷面：**逐字段等值**（不是 `>=`）。多一个字段，鸿蒙那边就有一处 `DayParser` 拿不到它；
  少一个字段，就是拆一份已发布的契约（139④(a)：改名 / 删口 / 开私口都算违契约）。

**鸿蒙那侧现在没跟着走的字段**（本刀按后端真值钉住，只报归属、不改别人那族文件）：
① 事件行的 `completed_at`（`WP-PLAN-COMPLETION-ACK`：完成态真源在事件上）——
   `entry/src/main/ets/model/AgendaEvent.ets` 的 `AgendaEvent` 没这个字段；
② 单日快照的 `avoid_windows`（避让时段）——同文件 `DayPayload` 没有，而 README:68 那行写着
   「含冲突、避让时段」⇒ 那份文档与它自己的模型也不一致。
计划层 10-22 退役后，单日快照不再有 `plan_items` / `plan_status`；鸿蒙 `DayParser` 读的是旧形状，
移动端整段押后，这里只钉后端真值。

跑法（免额度、免真机、免 LAN）：
`bash scripts/run_tests.sh tests/plobi/test_handset_interface_contract.py`
"""

from __future__ import annotations

from typing import Optional

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from plobi.agenda import router as agenda_router
from plobi.agenda import service as agenda_service
from plobi.agenda import store
from plobi.agenda.service import AgendaService
from plobi_cli.dashboard_auth import devices as dev
from plobi_cli.dashboard_auth import registry
from plobi_cli.dashboard_auth.base import DashboardAuthProvider, LoginStart, Session
from plobi_cli.dashboard_auth.cookies import SESSION_AT_COOKIE
from plobi_cli.dashboard_auth.pairing import HANDSET_API_PREFIX, install_pairing_auth
from plobi_cli.dashboard_auth.pairing_api import (
    DESKTOP_PAIRING_PREFIX,
    HANDSET_PAIR_PREFIX,
    desktop_router,
    handset_router,
)
from plobi_cli.dashboard_auth.scopes import enforce
from plobi_cli.dashboard_auth.token_auth import clear_token_routes, token_auth_middleware

DAY = "2026-10-16"
BROWSER_AT = "browser-access-token-for-contract-probe"

# ---- 载荷形状：后端今天的真值。改这里=改契约，得同步鸿蒙 README 与 model/AgendaEvent.ets ----

EVENT_ROW_FIELDS = {
    "completed_at",
    "confirm_seq",
    "confirm_seq_at",
    "created_at",
    "end_at",
    "evidence",
    "id",
    "kind",
    "location",
    "notes",
    "prev_value",
    "source",
    "start_at",
    "status",
    "title",
    "updated_at",
}
DAY_EVENT_ROW_FIELDS = EVENT_ROW_FIELDS | {"overlaps"}
DAY_TOP_LEVEL_FIELDS = {
    "avoid_windows",
    "conflicts",
    "date",
    "events",
}
CONFLICT_ROW_FIELDS = {"end_at", "left_id", "right_id", "start_at"}
REDEEM_RESPONSE_FIELDS = {"deviceId", "scopes", "token"}
REDEEM_SCOPES = {"agenda:read", "agenda:confirm", "task:read"}


class _BrowserProvider(DashboardAuthProvider):
    """The human login a gated bind demands; cookie-only, so it can never be a device."""

    name = "probe-browser"
    display_name = "Probe browser"
    supports_token = False

    def start_login(self, *, redirect_uri: str) -> LoginStart:
        return LoginStart(redirect_url=redirect_uri, cookie_payload={})

    def complete_login(self, *, code, state, code_verifier, redirect_uri) -> Session:
        return Session("user-1", "", "", "", self.name, 0, BROWSER_AT, "rt")

    def verify_session(self, *, access_token: str) -> Optional[Session]:
        if access_token != BROWSER_AT:
            return None
        return Session(
            "user-1", "u@example.test", "u", "org", self.name, 4_102_444_800, BROWSER_AT, "rt"
        )

    def refresh_session(self, *, refresh_token: str) -> Session:
        return Session("user-1", "", "", "", self.name, 0, BROWSER_AT, "rt")

    def revoke_session(self, *, refresh_token: str) -> None:
        return None


@pytest.fixture
def surface(tmp_path, monkeypatch):
    """Production's own mounts, on a tmp agenda DB and a tmp device store.

    Both agenda paths get pinned to the same tmp file on purpose: the list endpoints go through
    ``AgendaService`` while ``/day`` opens its own connection via ``store.agenda_db_path()``, so
    setting only one of them would quietly have the day assertions read the operator's real
    ``PLOBI_HOME`` DB instead of the fixture's.
    """
    clear_token_routes()
    registry.clear_providers()
    db = tmp_path / "agenda.db"

    monkeypatch.setenv("PLOBI_AGENDA_DB", str(db))
    monkeypatch.setattr(agenda_service, "_DEFAULT", AgendaService(db))
    monkeypatch.setattr(dev, "_DEFAULT", dev.DeviceStore(tmp_path))

    app = FastAPI()
    app.state.auth_required = True  # non-loopback shape: the cookie gate is authoritative

    @app.middleware("http")
    async def _cookie_gate(request, call_next):
        from plobi_cli.dashboard_auth.middleware import gated_auth_middleware

        return await gated_auth_middleware(request, call_next)

    app.middleware("http")(token_auth_middleware)
    app.include_router(agenda_router.router, prefix="/api/agenda")
    app.include_router(
        agenda_router.router,
        prefix=f"{HANDSET_API_PREFIX}/agenda",
        dependencies=[Depends(enforce)],
    )
    app.include_router(handset_router, prefix=HANDSET_PAIR_PREFIX)
    app.include_router(desktop_router, prefix=DESKTOP_PAIRING_PREFIX)

    registry.register_provider(_BrowserProvider())
    assert install_pairing_auth() is True

    store_ = dev.get_store()
    code, _expires = store_.issue_code()
    _first_id, token = store_.redeem(code, name="probe handset")

    with TestClient(app) as client:
        yield client, token, store_

    clear_token_routes()
    registry.clear_providers()


@pytest.fixture
def seeded(surface):
    """Overlapping confirmed pair, a point DDL, a pending row, a cancelled row."""
    client, token, store_ = surface
    service = agenda_service.get_service()
    left = service.create_manual(
        title="课 A", start_at=f"{DAY}T10:00:00", end_at=f"{DAY}T11:00:00", kind="class"
    )
    right = service.create_manual(
        title="组会 B", start_at=f"{DAY}T10:30:00", end_at=f"{DAY}T11:30:00"
    )
    ddl = service.create_manual(title="截 C", start_at=f"{DAY}T14:00:00")
    pending = service.create_manual(title="待确认 D", start_at=f"{DAY}T15:00:00")

    with service._conn() as conn:
        store.update_event(conn, pending.id, status="pending")
        cancelled = service.create_manual(title="取消 E", start_at=f"{DAY}T16:00:00")
        store.update_event(conn, cancelled.id, status="cancelled")
    return client, token, {
        "ddl": ddl.id,
        "left": left.id,
        "pending": pending.id,
        "right": right.id,
        "cancelled": cancelled.id,
    }


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _week() -> dict[str, str]:
    return {"from": f"{DAY}T00:00:00", "to": f"{DAY}T23:59:59"}


def test_read_surface_answers_and_matches_the_documented_lanes(seeded):
    """清单含已确认与待确认、不含已取消（README:66 那行的真值）；pending 口只有待确认。"""
    client, token, ids = seeded

    listing = client.get(f"{HANDSET_API_PREFIX}/agenda", params=_week(), headers=_bearer(token))

    assert listing.status_code == 200, listing.text
    assert {row["id"] for row in listing.json()} == {
        ids["left"],
        ids["right"],
        ids["ddl"],
        ids["pending"],
    }
    assert all(set(row) == EVENT_ROW_FIELDS for row in listing.json())

    pending = client.get(f"{HANDSET_API_PREFIX}/agenda/pending", headers=_bearer(token))

    assert pending.status_code == 200, pending.text
    assert [row["id"] for row in pending.json()] == [ids["pending"]]


def test_day_surface_field_for_field(seeded):
    """单日快照 = 桌面同一份 `day_surface`：四块、逐字段等值，冲突与避让都在这一个响应里。"""
    client, token, ids = seeded

    day = client.get(f"{HANDSET_API_PREFIX}/agenda/day", params={"date": DAY}, headers=_bearer(token))

    assert day.status_code == 200, day.text
    payload = day.json()

    assert set(payload) == DAY_TOP_LEVEL_FIELDS, "单日快照的块数变了：鸿蒙 DayParser 要跟着改"
    assert payload["date"] == DAY
    assert {row["id"] for row in payload["events"]} == {
        ids["left"],
        ids["right"],
        ids["ddl"],
        ids["pending"],
    }
    for row in payload["events"]:
        assert set(row) == DAY_EVENT_ROW_FIELDS, "事件行的字段集变了（含 completed_at / overlaps）"

    # 顺序由后端 start_at 决定，不假设 events[0] 是谁 ⇒ 按 id 取。
    left_row = next(row for row in payload["events"] if row["id"] == ids["left"])
    right_row = next(row for row in payload["events"] if row["id"] == ids["right"])

    assert set(left_row["overlaps"]) == {ids["right"]}
    assert set(right_row["overlaps"]) == {ids["left"]}
    ddl_row = next(row for row in payload["events"] if row["id"] == ids["ddl"])

    # 无 end_at 的点（DDL / 提醒）不算占满一小时 ⇒ 不制造冲突（C5 与 §3.1 同口径）。
    assert ddl_row["overlaps"] == []
    assert payload["conflicts"], "重叠的那两条应当贡献一行冲突"
    for conflict in payload["conflicts"]:
        assert set(conflict) == CONFLICT_ROW_FIELDS
    assert isinstance(payload["avoid_windows"], list)


def test_confirm_and_dismiss_are_the_only_device_writes(seeded):
    """两条写口之外，建 / 改 / 删对设备令牌一律不通（裁定 64.4：手机不必写看板就能看与批）。"""
    client, token, ids = seeded

    confirmed = client.post(
        f"{HANDSET_API_PREFIX}/agenda/{ids['pending']}/confirm", headers=_bearer(token)
    )

    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "confirmed"
    assert set(confirmed.json()) == EVENT_ROW_FIELDS

    dismissed = client.post(
        f"{HANDSET_API_PREFIX}/agenda/{ids['ddl']}/dismiss", headers=_bearer(token)
    )

    assert dismissed.status_code == 200, dismissed.text
    assert dismissed.json().get("id") == ids["ddl"]

    assert client.post(
        f"{HANDSET_API_PREFIX}/agenda",
        json={"title": "不该成立", "start_at": f"{DAY}T18:00:00"},
        headers=_bearer(token),
    ).status_code in (401, 403, 404, 405)
    assert client.patch(
        f"{HANDSET_API_PREFIX}/agenda/{ids['left']}", json={"title": "不该成立"}, headers=_bearer(token)
    ).status_code in (401, 403, 404, 405)
    assert client.delete(
        f"{HANDSET_API_PREFIX}/agenda/{ids['left']}", headers=_bearer(token)
    ).status_code in (401, 403, 404, 405)


def test_pairing_bootstrap_shape_and_single_device_revocation(surface):
    """兑换口的字段名与 scopes 是契约的一部分；撤销只伤这一台（这就是「可单独撤销」那半）。"""
    client, first_token, store_ = surface

    assert client.post(f"{HANDSET_PAIR_PREFIX}/redeem", json={"code": "000000"}).status_code in (
        400,
        401,
        422,
    )

    code, _expires = store_.issue_code()
    redeem = client.post(
        f"{HANDSET_PAIR_PREFIX}/redeem", json={"code": code, "deviceName": "第二个平板"}
    )

    assert redeem.status_code == 200, redeem.text
    body = redeem.json()

    assert set(body) == REDEEM_RESPONSE_FIELDS, "兑换响应字段变了 ⇒ PairingApi.parsePairResponse 要跟着改"
    assert set(body["scopes"]) == REDEEM_SCOPES
    assert body["token"] and body["token"] != first_token

    second = client.get(f"{HANDSET_API_PREFIX}/agenda", params=_week(), headers=_bearer(body["token"]))

    assert second.status_code == 200, second.text
    assert store_.revoke(body["deviceId"]) is True
    assert client.get(
        f"{HANDSET_API_PREFIX}/agenda", params=_week(), headers=_bearer(body["token"])
    ).status_code == 401
    # 第一台不受影响：撤销是逐台的，不是一把共用钥匙。
    assert client.get(
        f"{HANDSET_API_PREFIX}/agenda", params=_week(), headers=_bearer(first_token)
    ).status_code == 200


def test_device_token_never_opens_the_browser_surface(seeded):
    """两张面孔共用实现，但设备令牌只开设备面前缀（裁定 57 / 64.4）。"""
    client, token, _ids = seeded

    assert client.get("/api/agenda", params=_week(), headers=_bearer(token)).status_code == 401
    assert (
        client.get("/api/agenda", params=_week(), cookies={SESSION_AT_COOKIE: BROWSER_AT}).status_code == 200
    )
