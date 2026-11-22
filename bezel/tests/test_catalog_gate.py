"""The catalog refresh budget belongs to a credential/seat state, not to the process.

`_MODELS_MAX_PASSES` exists so a permanently-empty leg does not cost an endless poll, and
the give-up log has always said "until next sign-in". That promise was empty: nothing
reset `_models_refreshed` / `_models_passes`, so a leg that was empty at hour zero stayed
empty until the hub was restarted, and signing in only helped because `_recheck_after_login`
reached past the gate for that one leg.

The gate now validates itself against `_models_state_signature()` on entry: same world,
same verdict; the world moved (a credential went live, a seat appeared, a leg was enabled),
so the burned passes are meaningless and the budget starts over. These tests pin that
boundary in both directions — it must reopen on a real change and stay shut otherwise.
"""
from __future__ import annotations

import asyncio

from bezel.providers.base import Account, Capabilities, Credential
from bezel.registry import _MODELS_MAX_PASSES, Registry


class _Leg:
    """A leg whose catalog comes (or refuses to come) from `refresh_models`."""

    display_name = "Leg"

    def __init__(
        self,
        name: str = "leg",
        *,
        live: bool = True,
        seats: int = 1,
        models: tuple[str, ...] = (),
    ) -> None:
        self.name = name
        self.live = live
        self.seats = seats
        self.upstream = tuple(models)
        self.served_models: tuple[str, ...] = ()
        self.refreshes = 0
        self.asks = 0
        self.accounts: list[Account] = []
        self._seat()

    def _seat(self) -> None:
        self.accounts = [
            Account(id=f"{self.name}-{i}", provider=self.name, cred=Credential(access_token="T"))
            for i in range(self.seats)
        ]

    async def discover_accounts(self) -> list[Account]:
        self.asks += 1
        self._seat()  # the store was locked at boot; reading it again may find a seat
        return self.accounts

    async def refresh_models(self, acc: Account) -> list[str] | None:
        self.refreshes += 1
        self.served_models = tuple(self.upstream)
        return list(self.served_models) or None

    def capabilities(self) -> Capabilities:
        return Capabilities(stream=True, tools=True)

    def credential_health(self) -> tuple[bool, str]:
        return (True, "ok") if self.live else (False, "not_signed_in")


def _registry(*legs: _Leg) -> Registry:
    reg = Registry({"providers": {}}, None)
    for leg in legs:
        reg.providers[leg.name] = leg
    reg._rebuild_model_index()
    return reg


def _burn(reg: Registry, times: int) -> None:
    for _ in range(times):
        asyncio.run(reg.ensure_models_refreshed())


def test_the_budget_is_finite_for_a_world_that_does_not_move():
    leg = _Leg()  # live credential, a seat, and upstream still answering with nothing
    reg = _registry(leg)

    _burn(reg, _MODELS_MAX_PASSES + 3)

    assert leg.refreshes == _MODELS_MAX_PASSES, "the cap still caps"
    assert reg._models_refreshed is True


def test_a_credential_going_live_reopens_the_budget():
    leg = _Leg(live=False)  # signed out: not even a retry candidate
    reg = _registry(leg)

    _burn(reg, 1)
    assert reg._models_refreshed is True, "nothing to retry, so the gate may close"

    leg.live = True  # the sign-in the give-up log promised to wait for
    _burn(reg, 1)

    assert leg.refreshes == 2, "the new credential earned a fresh attempt"
    assert reg._models_refreshed is False, "a live seat with no catalog is a retry candidate again"


def test_a_seat_appearing_after_boot_reopens_the_budget():
    leg = _Leg(live=False, seats=0)
    reg = _registry(leg)

    _burn(reg, _MODELS_MAX_PASSES)
    assert leg.refreshes == 0, "no seat, no catalog to fetch"

    leg.seats = 1  # the locked store finally reads
    asyncio.run(reg.ensure_accounts_discovered())
    _burn(reg, 1)

    assert leg.refreshes == 1, "the seat brought a reason to ask again"


def test_a_leg_enabled_on_demand_reopens_the_budget():
    leg = _Leg()
    reg = _registry(leg)
    _burn(reg, _MODELS_MAX_PASSES)
    assert leg.refreshes == _MODELS_MAX_PASSES

    late = _Leg("late")
    reg.providers[late.name] = late  # /plobi/auth/<app> adds a leg mid-flight
    _burn(reg, 1)

    assert late.refreshes == 1, "the new leg is asked on its own account"
    assert leg.refreshes == _MODELS_MAX_PASSES + 1, "and the old leg gets one more look"


def test_a_moved_world_does_not_reopen_a_gate_that_was_never_spent():
    leg = _Leg()
    reg = _registry(leg)
    reg._models_refreshed = True  # a caller that already knows the catalog is loaded

    _burn(reg, 2)

    assert leg.refreshes == 0, "adopting the first signature must not look like a change"


def test_the_poll_path_asks_again_after_a_sign_in():
    """`models_refresh_task` used to stop re-arming the moment the gate latched, so the
    picker's own poll could never discover that the world had moved. The 900s quota loop
    was the only thing left to notice, i.e. every catalog recovery was up to 15 minutes late.
    """
    leg = _Leg(live=False)  # signed out at boot
    reg = _registry(leg)

    async def _ask() -> None:
        await reg.models_refresh_task()

    asyncio.run(_ask())
    assert reg._models_refreshed is True, "nothing to retry while it is signed out"

    reg._models_attempt -= reg._MODELS_RETRY_AFTER + 1  # the poll floor lapses
    leg.live = True  # the user signed in out of band, no /plobi/auth call involved
    asyncio.run(_ask())

    assert leg.refreshes == 2, "the poll re-armed the gate on its own"
