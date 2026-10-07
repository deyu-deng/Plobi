"""Workbuddy adapter — prefer CLI, fall back to GUI (合规优先组合).

Workbuddy ships (in some builds) a CLI you can spawn like any other agent; in
others it is only a desktop GUI. This composite provider implements the
"优先 CLI，如果没有就 GUI" rule WITHOUT two separate config blocks:

  - It builds an internal WorkbuddyCliProvider and an internal
    WorkbuddyGuiProvider from the `cli:` / `gui:` sub-configs of the `workbuddy:`
    provider block.
  - `discover_accounts()` exposes CLI accounts first (preferred) and only appends
    GUI accounts when the CLI is unavailable. `prefer: gui` flips the order.
  - chat / chat_stream / ensure_fresh delegate to whichever sub-provider owns the
    account. Colliding account ids are renamed (`-cli` / `-gui` suffix) so the
    scheduler can pin a conversation to one seat unambiguously.

If `cli:` is omitted, the whole `workbuddy:` block is treated as CLI config. If
`gui:` is omitted, the GUI route is skipped. This is the recommended compliant
entry point — register it under the `workbuddy` key.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from .base import Account, Capabilities, Provider, UpstreamError
from .workbuddy_cli import WorkbuddyCliProvider
from .workbuddy_gui import WorkbuddyGuiProvider


class WorkbuddyHybridProvider(Provider):
    name = "workbuddy"
    served_models: tuple[str, ...] = ()
    # Prefer CLI (compliant); GUI is gray. Report compliant when CLI seats exist.
    default_capabilities = Capabilities(
        stream=True,
        tools=False,
        vision=False,
        embeddings=False,
        sessionful=False,
        compliance="compliant",
    )

    def __init__(self, config, http):
        super().__init__(config, http)
        self.served_models = tuple(config.get("models", ["workbuddy/default"]))
        self.prefer = config.get("prefer", "cli")

        cli_cfg = dict(config.get("cli") or config)  # omit `cli:` => whole block is CLI cfg
        cli_cfg.setdefault("models", list(self.served_models))
        gui_cfg = dict(config.get("gui") or {})
        gui_cfg.setdefault("models", list(self.served_models))

        self._cli = WorkbuddyCliProvider(cli_cfg, http)
        # The hybrid owns the public id space, so the inner CLI must discover models
        # under `workbuddy/...`, not its own registry key `workbuddy_cli/...`.
        self._cli.model_prefix = self.name
        self._gui = WorkbuddyGuiProvider(gui_cfg, http) if gui_cfg else None
        # acc.id -> owning sub-provider (filled in discover_accounts)
        self._owner: dict[str, Provider] = {}

    # --- discovery -------------------------------------------------------
    async def discover_accounts(self) -> list[Account]:
        cli_accs = await self._cli.discover_accounts()
        gui_accs = await self._gui.discover_accounts() if self._gui else []

        first, second = (cli_accs, gui_accs) if self.prefer == "cli" else (gui_accs, cli_accs)

        self._owner = {}
        ordered: list[Account] = []
        for src, accs in ((self._cli, first), (self._gui, second)):
            for acc in accs:
                suffix = "cli" if src is self._cli else "gui"
                key = f"{acc.id}-{suffix}"
                while key in self._owner:  # de-dupe collisions
                    key += "x"
                acc.id = key
                self._owner[key] = src
                ordered.append(acc)

        self.accounts = ordered
        return ordered

    # --- delegation ------------------------------------------------------
    async def ensure_fresh(self, acc: Account) -> None:
        owner = self._owner.get(acc.id)
        if owner is None:
            raise UpstreamError(503, f"{self.name}: unknown account {acc.id}")
        await owner.ensure_fresh(acc)

    async def refresh_models(self, acc: Account) -> list[str] | None:
        """Forward the catalog read to the owning sub-provider and copy it up.

        The Registry reads ``served_models`` off *this* object, so a successful
        discovery by the inner CLI has to be lifted here — otherwise the
        recommended ``workbuddy`` route would keep answering from its config seed
        while only the pure-CLI provider went out to look.
        """
        owner = self._owner.get(acc.id)
        if owner is None:
            return None
        short = await owner.refresh_models(acc)
        if not short:
            return None
        self.served_models = tuple(f"{self.name}/{m}" for m in short)
        self.models_refreshed = True
        return short

    async def chat(self, acc: Account, oai_req: dict) -> dict:
        owner = self._owner.get(acc.id)
        if owner is None:
            raise UpstreamError(503, f"{self.name}: unknown account {acc.id}")
        return await owner.chat(acc, oai_req)

    async def chat_stream(self, acc: Account, oai_req: dict) -> AsyncIterator[dict]:
        owner = self._owner.get(acc.id)
        if owner is None:
            raise UpstreamError(503, f"{self.name}: unknown account {acc.id}")
        async for chunk in owner.chat_stream(acc, oai_req):
            yield chunk
