"""A seat that appears after boot must reach the running hub, not the next restart.

Measured 2026-11-08 on the installed 0.3.0: the hub booted while Kiro's sqlite was
unreadable, so the leg had zero seats and zero models; a minute later the same store read
fine (one account, a live token) in a fresh process — but the running hub kept reporting
nothing until restarted. Accounts are discovered once in `build()`, so the refresh loop
now re-asks seat-less legs; this pins that behaviour and its two boundaries (a leg that
already has seats is not re-asked, and a store that raises is a retry, not a crash).
"""
from __future__ import annotations

import asyncio

from quota_hub.providers.base import Account, Capabilities, Credential
from quota_hub.registry import Registry


class _LateStore:
    """A leg whose credential store only becomes readable on the second ask."""

    name = "late"
    display_name = "Late"

    def __init__(self, readable_on: int = 2, raises: bool = False, name: str = "late") -> None:
        self.name = name
        self.accounts: list[Account] = []
        self.served_models: tuple[str, ...] = ()
        self.asks = 0
        self.readable_on = readable_on
        self.raises = raises

    async def discover_accounts(self) -> list[Account]:
        self.asks += 1
        if self.raises:
            raise OSError("database is locked")
        if self.asks < self.readable_on:
            return []
        acc = Account(id=f"{self.name}-default", provider=self.name, cred=Credential(access_token="T"))
        self.accounts = [acc]
        self.served_models = (f"{self.name}/one",)
        return self.accounts

    def capabilities(self) -> Capabilities:
        return Capabilities(stream=True, tools=True)

    def credential_health(self):
        return True, "ok"


def _registry(*legs) -> Registry:
    reg = Registry({"providers": {}}, None)
    for leg in legs:
        reg.providers[leg.name] = leg
    reg._rebuild_model_index()
    return reg


def test_a_seat_that_appears_after_boot_is_picked_up_without_a_restart():
    leg = _LateStore(readable_on=2)
    reg = _registry(leg)
    assert reg.model_to_provider == {}

    asyncio.run(reg.ensure_accounts_discovered())
    assert leg.asks == 1 and not leg.accounts, "first ask: the store is still locked"

    asyncio.run(reg.ensure_accounts_discovered())

    assert len(leg.accounts) == 1, "the second ask found the seat"
    assert reg.model_to_provider == {"late/one": "late"}, reg.model_to_provider


def test_a_leg_that_already_has_seats_is_not_re_asked():
    leg = _LateStore(readable_on=1)
    asyncio.run(leg.discover_accounts())
    reg = _registry(leg)
    before = leg.asks

    asyncio.run(reg.ensure_accounts_discovered())
    asyncio.run(reg.ensure_accounts_discovered())

    assert leg.asks == before, "re-discovery is only for seat-less legs"


def test_a_store_that_raises_is_a_retry_not_a_crash():
    bad = _LateStore(raises=True, name="locked")
    good = _LateStore(readable_on=1, name="open")
    reg = _registry(bad, good)

    asyncio.run(reg.ensure_accounts_discovered())

    assert bad.asks == 1 and not bad.accounts
    assert len(good.accounts) == 1, "one locked store must not stop the others"
