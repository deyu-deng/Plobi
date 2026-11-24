"""Magpie's own local gateway, as one of our legs.

magpie (https://github.com/yetone/magpie, MIT) is a local multi-model gateway: it
keeps the vendor subscriptions — WorkBuddy, Qoder, OpenCode Zen, Copilot, Codex,
Claude Code, Kiro, Cline … — behind one OpenAI-compatible endpoint, and since
2026-10 it serves most of them through **community provider plugins**
(`magpie-community/plugins`: `qoder`, `workbuddy`, `zen-free`, `kiro`, `cline`,
`copilot`, `cursor`, `devin`, `grok`, …). Its own docs put it exactly where we
want to put it: *"your app needs one provider entry, not one per vendor"*.

What this leg does, and nothing more:

* finds the gateway (the port lives in magpie's own `settings.json`;
  `GET /api/hello` answers `{"name":"magpie", …}` so we know it is there);
* asks it for its catalogue (`GET /v1/models`) and publishes every row under our
  own namespace (``magpie/<magpie's own id>``). Each row carries **that model's**
  window, thinking levels and output cap, and those go into `tuning()` — the
  numbers the picker needs and that a hand-written list can only guess. This is
  the whole point: the vendor adapters are somebody else's maintenance now, so
  a model that gets renamed or resized stops being our problem;
* sends the turn to `POST /v1/chat/completions` under our own key name
  (`magpie-plobi`), so the calls are counted under Plobi in magpie's own usage
  and can be held to a budget there.

It is **additive**. A machine without magpie running simply has no such leg:
we download nothing, sign in to nothing, and invent no roster. The legs we
still keep stay as the fallback for the vendors they serve.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx

from ...base import Account, Capabilities, Credential, Provider, UpstreamError
from . import their_list

#: 这条腿的故障没有别的证据可查（网关自己的 usage 只记走完的回合），所以每次
#: "200 而 body 不成样子"都要在 desktop.log 里留下状态码、content-type 和前 400 字，
#: 否则下次还是只能对着「Error code: 502」猜。
_log = logging.getLogger(__name__)

#: Where magpie keeps its settings; the port is in there. Their docs: the file is
#: ``~/.config/magpie/settings.json`` — "the same path under the user's folder on
#: Windows" — and ``$XDG_CONFIG_HOME/magpie`` when that is set. Note the
#: difference: with ``XDG_CONFIG_HOME`` set it is ``magpie`` directly under it,
#: NOT ``.config/magpie`` under it. Getting this wrong is what made the desktop
#: side write the port on the data volume while this side read the user's home.
SETTINGS_DIR = "magpie"
SETTINGS_FILE = "settings.json"
DEFAULT_PORT = 3425
#: The port the desktop pinned for the copy it started. When it is here, it wins:
#: the sidecar has already made sure nothing else owns that port.
PORT_ENV = "PLOBI_MAGPIE_PORT"
#: The key we send. Their docs: "Send `magpie-<your-app-id>` … and every call is
#: counted as `acme` in Usage, the Requests log and the routing rules".
DEFAULT_KEY_NAME = "magpie-plobi"
#: The endpoint that says "this is magpie" (`{"name":"magpie","version":"…"}`).
HELLO_PATH = "/api/hello"
MODELS_PATH = "/v1/models"
CHAT_PATH = "/v1/chat/completions"
#: Our own provider table — the truth about what a card is called, how we sign in,
#: and which id we publish. Their CLI only says which providers exist today.
PROVIDERS_FILE = Path(__file__).with_name("providers.yaml")
#: How long a health verdict is trusted before it is asked again. The registry
#: asks once per model row, so without this a catalogue of a hundred models
#: would probe the gateway a hundred times.
HEALTH_TTL_SECONDS = 5.0


class MagpieProvider(Provider):
    name = "magpie"
    display_name = "Magpie"
    # The umbrella itself publishes nothing: every model row belongs to the
    # vendor leg it was discovered for (see ``MagpieProviderLeg``).
    publishes_models = False
    # The gateway speaks OpenAI, streams, and passes tools through to the vendor
    # (their `zen-free` plugin even needs a tool list to get an answer at all).
    default_capabilities = Capabilities(
        stream=True,
        tools=True,
        vision=False,
        embeddings=False,
        sessionful=False,
    )

    def __init__(self, config: dict, http: httpx.AsyncClient):
        super().__init__(config, http)
        self.api_key = str(config.get("api_key") or DEFAULT_KEY_NAME)
        self.timeout = float(config.get("timeout", 120.0))
        #: The vendor's own rows, keyed by the id we publish them under.
        self._model_rows: dict[str, dict] = {}
        #: (live, reason, at) — the last verdict about the gateway.
        self._health: tuple[bool, str, float] | None = None

    # --- where the gateway is ---------------------------------------------
    def _config_root(self) -> Path:
        xdg = (os.environ.get("XDG_CONFIG_HOME") or "").strip()
        return Path(xdg) if xdg else Path.home() / ".config"

    def _settings_path(self) -> Path:
        return self._config_root() / SETTINGS_DIR / SETTINGS_FILE

    def _port(self) -> int:
        """The port magpie listens on.

        Three sources, most authoritative first: the port the desktop pinned for
        the copy it started (``PLOBI_MAGPIE_PORT``), then magpie's own settings
        file — which the desktop writes for us anyway — then their default.
        """
        env_port = str(os.environ.get(PORT_ENV) or "").strip()
        if env_port.isdigit():
            port = int(env_port)
            if 0 < port < 65536:
                return port
        try:
            data = json.loads(self._settings_path().read_text(encoding="utf-8"))
        except Exception:
            return DEFAULT_PORT
        port = data.get("port") if isinstance(data, dict) else None
        if isinstance(port, bool) or not isinstance(port, int) or port <= 0:
            return DEFAULT_PORT
        return port

    def _base_url(self) -> str:
        explicit = str(self.config.get("base_url") or "").strip()
        if explicit:
            return explicit.rstrip("/")
        return f"http://127.0.0.1:{self._port()}"

    def _headers(self) -> dict[str, str]:
        return {
            "authorization": f"Bearer {self.api_key}",
            "accept": "application/json",
            "content-type": "application/json",
        }

    # --- is it there -------------------------------------------------------
    def credential_health(self) -> tuple[bool, str]:
        """Can the gateway answer right now?

        The gateway needs no credential of ours — our key is a name, so the only
        question is whether it is running. The verdict is cached for a few
        seconds because the registry asks it once per model row.
        """
        now = time.time()
        if self._health is not None and now - self._health[2] < HEALTH_TTL_SECONDS:
            return self._health[0], self._health[1]
        live, reason = self._probe_hello()
        self._health = (live, reason, now)
        return live, reason

    def _hello_body(self) -> dict | None:
        """The gateway's answer to `/api/hello`, or None when it did not answer."""
        try:
            with httpx.Client(timeout=1.5, trust_env=False) as client:
                res = client.get(self._base_url() + HELLO_PATH)
        except Exception:
            return None
        if res.status_code != 200:
            return None
        try:
            data = res.json()
        except Exception:
            return None
        return data if isinstance(data, dict) else None

    def _probe_hello(self) -> tuple[bool, str]:
        """Is the gateway there?

        The reason code is one the desktop already knows how to say: a machine
        word we invent would show up raw on the screen (the card's tooltip
        carries the leg's own reason verbatim), and the four locales own that
        wording. "We found no local credential for it" is what it means for a
        gateway that holds the vendors' logins for us.
        """
        data = self._hello_body()
        if data is None:
            return False, "no_desktop_credential"
        name = str(data.get("name") or "").strip().lower()
        if name != "magpie":
            return False, "no_desktop_credential"
        return True, "ok"

    def present(self) -> bool:
        """Magpie is **not** a product the user configures, so it gets no card.

        The Providers screen lists the things he signs in to — WorkBuddy,
        Qoder, OpenCode Free … — and each of those keeps its own card and its
        own "+" (user, 11-09 20:0x: 「不要这样弄。我说了前端还是用我们自己的操作逻辑。
        要配置 workbuddy 就要在相应的卡片点击加号配置的」). Magpie is the layer
        **behind** those cards: it ships with us and it is ours to configure,
        so putting it on the same list would be a second place to configure the
        same thing — and would hide which vendor a model really came from.

        It is also why this leg is not in ``ADAPTED_APPS``: that table is the
        card list, and this is not a card.
        """
        return False

    def login_plan(self) -> dict:
        return {
            "kind": "local_session",
            "name": self.display_name,
            "reason": "它跑在本机那个 Magpie 网关上",
        }

    # --- accounts ----------------------------------------------------------
    async def discover_accounts(self) -> list[Account]:
        """One seat: the local gateway. No credential of ours is needed."""
        live, reason = self.credential_health()
        self._health = (live, reason, time.time())
        self.accounts = [
            Account(
                id="magpie-local",
                provider=self.name,
                label="Magpie 本地网关",
                cred=Credential(access_token=self.api_key),
            )
        ]
        return self.accounts

    async def ensure_fresh(self, acc: Account) -> None:
        """Nothing to refresh: the gateway takes any key, and ours is a name."""
        return None

    # --- the catalogue -----------------------------------------------------
    async def refresh_models(self, acc: Account) -> list[str] | None:
        """magpie's own roster, read now.

        Their `/v1/models` is the one list worth reading: "List them from there
        rather than shipping a list: the user adds and removes providers in
        magpie." Every row is published under our namespace with that model's
        own window, thinking levels and output cap.
        """
        base = self._base_url()
        try:
            res = await self.http.get(
                base + MODELS_PATH,
                headers=self._headers(),
                timeout=self.timeout,
            )
        except Exception:
            return None
        if res.status_code != 200:
            return None
        try:
            payload = res.json()
        except Exception:
            return None

        rows = payload.get("data") if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            return None

        picked: dict[str, dict] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            mid = str(row.get("id") or "").strip()
            if not mid:
                continue
            picked[f"{self.name}/{mid}"] = row
        if not picked:
            return None

        # The umbrella publishes **nothing**: every row belongs to the provider
        # leg for its own vendor (see `MagpieProviderLeg`), so publishing them
        # here too would list the same models twice under two names.
        self._model_rows = {}
        self.served_models = ()
        self._health = (True, "ok", time.time())
        return None

    # --- the provider list -------------------------------------------------
    #: Their HTTP surface has no endpoint for the *roster* — it serves models and
    #: turns. Which providers exist therefore has to be assembled from three
    #: places (see `their_list`): the live catalogue's model prefixes, the CLI's
    #: printed table, and magpie's own on-disk roster (the only one that knows the
    #: built-in providers). The catalogue is cheap and re-read in a minute; the
    #: CLI boots their plugin host, so its answer is trusted for ten.
    SERVING_TTL_SECONDS = 60.0
    ROSTER_TTL_SECONDS = 60.0
    ROSTER_CLI_TTL_SECONDS = 600.0

    def _binary(self) -> str | None:
        """Where magpie's own CLI is, if we can find it.

        Two sources only: the path the desktop handed us (``PLOBI_MAGPIE_BINARY``
        — the copy that ships inside the app, so both processes talk to the same
        one), and an explicit ``binary`` in the config.

        Deliberately **no PATH lookup**. Falling back to whatever ``magpie-cli``
        the machine happens to have installed means running a stranger's copy:
        its data directory, its port, its providers — and then reporting that
        state as if it were ours. Missing means missing; the card says so.
        """
        env_path = str(os.environ.get("PLOBI_MAGPIE_BINARY") or "").strip()
        if env_path and Path(env_path).is_file():
            return env_path
        explicit = str(self.config.get("binary") or "").strip()
        if explicit and Path(explicit).is_file():
            return explicit
        return None

    async def provider_specs(self) -> list[dict]:
        """One card per provider magpie actually has — **their** roster decides.

        The set is theirs; the cosmetics are ours:

        * ``GET /v1/models`` prefixes — which providers can serve right now;
        * ``magpie plugin list`` — the vendor's own name and sign-in methods;
        * ``providers-state.json`` — the built-in providers no plugin lists
          (WorkBuddy: 18 models, ``ready``, and no plugin row anywhere).

        A provider our cosmetics table names gets our four-locale label and our
        sign-in kind. One we have never seen keeps the name its vendor reported
        and defaults to a browser round trip — which is what their plugins do.
        Nothing is hidden for being unrecognised: that is precisely the case a
        fixed table used to swallow, so a provider added upstream never reached
        the screen.

        When none of the three answers (magpie is not on this machine), the
        cosmetics table is all we have and every card in it still shows, saying
        「Magpie 没起来」 instead of pretending the vendor was never set up.
        """
        serving = await self._serving_prefixes()
        theirs = self._their_providers(serving)
        table = self._our_table()

        if not theirs:
            return [self._card_spec(pid, row, present=None) for pid, row in table.items()]

        claimed = {str(row.get("upstream") or pid): pid for pid, row in table.items()}
        order = {pid: index for index, pid in enumerate(table)}

        named: list[tuple[int, dict]] = []
        unnamed: list[tuple[str, dict]] = []
        # Which plugins are loaded right now. Their roster outlives an uninstall
        # (accounts are kept), so this is the only way to tell a provider that is
        # merely listed from one whose plugin is actually here.
        loaded = self._installed_plugins()
        for upstream_id, row in theirs.items():
            ours = claimed.get(upstream_id)
            plugin = str(row.get("spec") or row.get("plugin") or "").strip() or None
            installed = None
            if plugin is not None and loaded is not None:
                installed = their_list.bare_spec(plugin) in loaded
            spec = self._card_spec(
                ours or upstream_id,
                table.get(ours, {}) if ours else {},
                present=True,
                upstream_id=upstream_id,
                reported=row,
                installed=installed,
                plugin=plugin,
            )
            if ours:
                named.append((order.get(ours, len(order)), spec))
            else:
                unnamed.append((upstream_id, spec))
        named.sort(key=lambda pair: pair[0])
        unnamed.sort(key=lambda pair: pair[0])
        return [spec for _, spec in named] + [spec for _, spec in unnamed]

    def _our_table(self) -> dict[str, dict]:
        """Our cosmetics table, read once per process.

        It is **not** the roster: which providers exist is magpie's answer, not
        this file's. An empty table is survivable — every provider then shows
        under the name its vendor reported — so a broken YAML costs wording,
        never the screen.
        """
        cached = getattr(self, "_table_cache", None)
        if cached is not None:
            return cached
        table: dict[str, dict] = {}
        try:
            import yaml  # optional at runtime, present in the venv

            raw = yaml.safe_load(PROVIDERS_FILE.read_text(encoding="utf-8")) or {}
            for key, row in (raw.get("providers") or {}).items():
                if isinstance(row, dict):
                    table[str(key)] = row
        except Exception:
            # No table, no wording of ours: cards still appear, named as magpie
            # named them. Never invent a roster out of this failure.
            table = {}
        self._table_cache = table
        return table

    def _card_spec(
        self,
        provider_id: str,
        cosmetics: dict,
        *,
        present: bool | None,
        upstream_id: str | None = None,
        reported: dict | None = None,
        installed: bool | None = None,
        plugin: str | None = None,
    ) -> dict:
        """One row of ``provider_specs``: their identity, our label.

        ``cosmetics`` is our table's row for this vendor when we have one. The
        four-locale wording lives in the front end keyed by ``intro_key``, so
        this side only has to say *which* key — never the sentence itself.
        """
        reported = reported or {}
        name = str(cosmetics.get("name") or reported.get("name") or provider_id)
        return {
            "id": provider_id,
            "upstream": str(cosmetics.get("upstream") or upstream_id or provider_id),
            "name": name,
            "card": bool(cosmetics.get("card", True)),
            "login": str(cosmetics.get("login") or self._login_kind(reported) or "oauth"),
            # Whether magpie has it today. None = nobody could tell us.
            "present": present,
            # The key the view looks up for its one-line subtitle. Our table wins,
            # so renaming our own key does not re-translate a vendor we already named.
            "intro_key": str(cosmetics.get("intro_key") or provider_id),
            # Signed in as far as their roster can say. The card still asks the
            # gateway for the live answer; this only spares a round trip.
            "signed_in": reported.get("signedIn"),
            # Whether the plugin behind this provider is loaded *right now*. Their
            # roster keeps a removed plugin's providers — the accounts are kept on
            # purpose across an uninstall — so `present` alone would keep offering a
            # sign-in that can never finish. None = nobody could tell us: leave as is.
            "installed": installed,
            # The plugin spec, so the card can offer to put it back.
            "plugin": plugin,
            "install_command": (
                f"magpie plugin add {plugin}" if plugin and installed is False else None
            ),
        }

    @staticmethod
    def _login_kind(reported: dict) -> str:
        """How this provider is signed in, read off their ``methods`` list.

        ``api`` only when *every* method is a key the user pastes — a plugin
        that also offers a browser round trip is ``oauth``, and their plugins
        all offer one.
        """
        methods = [m for m in (reported.get("methods") or []) if isinstance(m, dict)]
        if not methods:
            return ""
        kinds = {str(m.get("type") or "").strip().lower() for m in methods}
        return "api" if kinds == {"api"} else "oauth"

    async def _serving_prefixes(self) -> dict[str, dict] | None:
        """The provider prefixes in the live catalogue — None when it is down.

        ``GET /v1/models`` is the only source that is both online and a contract,
        so it seeds the roster. It says nothing about names or sign-in, and an
        empty answer means "not answering", never "they have no providers".
        """
        cached = getattr(self, "_serving_cache", None)
        now = time.time()
        if cached is not None and now - cached[1] < self.SERVING_TTL_SECONDS:
            return cached[0]
        rows = await self.read_catalogue()
        prefixes = their_list.providers_from_model_ids(list(rows)) if rows else None
        result = prefixes or None
        self._serving_cache = (result, now)
        return result

    def _their_providers(self, serving: dict[str, dict] | None = None) -> dict[str, dict] | None:
        """Every provider magpie has today, merged from all three sources.

        ``None`` means no source could be read at all ("we could not ask"). An
        empty dict is never returned: a plugin host that has not finished loading
        must not empty the user's screen.

        Precedence, and why it is this way round: the live catalogue decides
        *membership* (it is the only live answer), the CLI contributes the names
        and methods nothing else carries, and the on-disk roster contributes the
        built-in providers that no plugin row ever mentions.
        """
        cached = getattr(self, "_their_providers_cache", None)
        now = time.time()
        if serving is None and cached is not None and now - cached[1] < self.ROSTER_TTL_SECONDS:
            return cached[0]

        merged: dict[str, dict] = {}
        answered = False

        # The built-ins: WorkBuddy never appears in `plugin list`.
        state = their_list.read_provider_table(self._config_root())
        # A file that parses to nothing is not an answer: it is a file magpie has
        # not finished writing yet. Only rows count.
        if state:
            answered = True
            for pid, row in state.items():
                ready = row.get("ready")
                merged[pid] = {
                    "id": pid,
                    "name": str(row.get("name") or "").strip() or None,
                    "signedIn": ready if isinstance(ready, bool) else None,
                    "source": "state",
                    # The plugin this provider came from. Kept so a card can tell a
                    # remembered provider from a loaded plugin, and offer to add it back.
                    "spec": str(row.get("spec") or "").strip() or None,
                }
                # The file also carries the sign-in methods when the built-in wrote
                # them; dropping them here sends the login back to their terminal menu.
                if row.get("methods"):
                    merged[pid]["methods"] = row["methods"]

        # The plugins: the only source carrying the vendor's own name and methods.
        cli = self._their_rows_via_cli()
        if cli:
            answered = True
            for pid, row in cli.items():
                target = merged.setdefault(pid, {"id": pid})
                for key in ("name", "methods", "signedIn", "plugin", "models", "model_count"):
                    if row.get(key) not in (None, "", [], {}):
                        target.setdefault(key, row[key])

        # The live catalogue: membership, plus any provider a stale file missed.
        if serving:
            answered = True
            for pid in serving:
                merged.setdefault(pid, {"id": pid, "source": "catalogue"})

        if not answered:
            return None
        self._their_providers_cache = (merged, now)
        return merged

    def _installed_plugins(self) -> set[str] | None:
        """The plugin specs loaded right now, or None when the CLI would not answer.

        ``plugin list`` is the only source that distinguishes "magpie remembers this
        provider" from "this provider's plugin is here": removing a plugin keeps its
        providers on the roster, so the roster answers yes for both. Cached like the
        roster — a plugin install is not a per-request event.
        """
        cached = getattr(self, "_installed_plugins_cache", None)
        now = time.time()
        if cached is not None and now - cached[1] < self.ROSTER_CLI_TTL_SECONDS:
            return cached[0]
        # Their own install list first: no process, no timeout, no 20s stall on a
        # cold CLI. The CLI is only for a release that keeps it somewhere else.
        found = their_list.read_installed_plugins(self._config_root())
        if found is None:
            binary = self._binary()
            found = their_list.cli_installed_plugins(binary) if binary else None
        self._installed_plugins_cache = (found, now)
        return found

    def _their_provider_ids(self) -> set[str] | None:
        """Just the ids of the merged roster, or None when nothing answered."""
        rows = self._their_providers()
        return None if rows is None else set(rows)

    def _their_rows_via_cli(self) -> dict[str, dict] | None:
        """Run their CLI once and read the provider rows out of its table.

        Their CLI boots the plugin host — slow, and one plugin that cannot load
        can stop it printing anything — so its answer outlives the file reads.
        """
        cached = getattr(self, "_their_cli_rows_cache", None)
        now = time.time()
        if cached is not None and now - cached[1] < self.ROSTER_CLI_TTL_SECONDS:
            return cached[0]
        binary = self._binary()
        rows = None
        if binary:
            rows = their_list.cli_providers(
                binary,
                env={**os.environ, "DO_NOT_TRACK": "1"},
                timeout=120.0,
            )
        self._their_cli_rows_cache = (rows, now)
        return rows

    def unclaimed_providers(self) -> list[dict]:
        """Providers magpie has that our table has no cosmetics for.

        They still get a card — under the name their vendor reported — so this is
        a to-do list for a human (``check_magpie_providers.py`` prints it), not a
        filter. Naming one in ``providers.yaml`` only improves the label.
        """
        rows = self._their_providers()
        if not rows:
            return []
        ours = {str(row.get("upstream") or pid) for pid, row in self._our_table().items()}
        return [
            {"id": pid, "name": str(rows[pid].get("name") or "")}
            for pid in sorted(set(rows) - ours)
        ]

    async def read_catalogue(self) -> dict[str, dict]:
        """magpie's own rows, keyed by their id — one read for every leg."""
        base = self._base_url()
        try:
            res = await self.http.get(
                base + MODELS_PATH,
                headers=self._headers(),
                timeout=self.timeout,
            )
        except Exception:
            return {}
        if res.status_code != 200:
            return {}
        try:
            payload = res.json()
        except Exception:
            return {}
        rows = payload.get("data") if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            return {}
        picked: dict[str, dict] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            mid = str(row.get("id") or "").strip()
            if mid:
                picked[mid] = row
        return picked

    def tuning(self, model: str) -> dict:
        """What this model lets the user change, from magpie's own row.

        Their catalogue writes, per model: `context_window`,
        `max_output_tokens`, and the thinking levels it sells
        (`supported_reasoning_levels`, or the same as plain names in
        `reasoning_levels`). Those are the numbers the picker asks for — the
        whole reason a leg that reads a live catalogue can answer and a
        hand-written list cannot.
        """
        row = self._model_rows.get(model) or {}
        out: dict[str, Any] = {}

        window = row.get("context_window")
        if isinstance(window, int) and not isinstance(window, bool) and window > 0:
            out["context"] = {
                "adjustable": False,
                "choices": [window],
                "default": window,
                "max": window,
            }

        cap = row.get("max_output_tokens")
        if isinstance(cap, (int, float)) and not isinstance(cap, bool) and cap > 0:
            out["max_output"] = int(cap)

        efforts = self._efforts_of(row)
        if efforts:
            out["efforts"] = efforts
            out["default_effort"] = "medium" if "medium" in efforts else efforts[len(efforts) // 2]
        return out

    @staticmethod
    def _efforts_of(row: dict) -> list[str]:
        """The thinking levels this model sells, in their own words.

        `supported_reasoning_levels` may be objects (`[{"effort":"low"},…]`) or
        plain names; `reasoning_levels` is the plain-name list. Neither means
        "this model has no levels" — it means the catalogue did not say.
        """
        names: list[str] = []
        raw = row.get("supported_reasoning_levels")
        if isinstance(raw, list):
            for item in raw:
                name = str(item.get("effort") if isinstance(item, dict) else item or "").strip().lower()
                if name and name not in names:
                    names.append(name)
        if not names:
            plain = row.get("reasoning_levels")
            if isinstance(plain, list):
                for item in plain:
                    name = str(item or "").strip().lower()
                    if name and name not in names:
                        names.append(name)
        return names

    # --- the turn ----------------------------------------------------------
    def _map_model(self, model: str) -> str:
        """magpie's own id: our namespace is one segment in front of theirs."""
        name = str(model or "")
        prefix = f"{self.name}/"
        if name.startswith(prefix):
            return name[len(prefix):]
        return name

    def _body(self, oai_req: dict, *, stream: bool) -> dict:
        """The request, in the shape magpie answers (OpenAI Chat Completions)."""
        body: dict[str, Any] = {"model": self._map_model(oai_req.get("model", ""))}
        for key in (
            "messages",
            "tools",
            "tool_choice",
            "parallel_tool_calls",
            "temperature",
            "top_p",
            "max_tokens",
            "stop",
            "reasoning_effort",
        ):
            if oai_req.get(key) is not None:
                body[key] = oai_req[key]
        body["stream"] = stream
        return body

    @staticmethod
    def _not_served(status: int, oai_req: dict) -> UpstreamError | None:
        """magpie answers 404 for a model it does not serve.

        Folding that into an empty stream is how a model id that no longer
        exists looks like an upstream fault ("Provider returned an empty stream
        with no finish_reason"). Say the truth instead: the model is not there.
        """
        if status == 404:
            model = str(oai_req.get("model") or "")
            return UpstreamError(404, f"magpie: no such model: {model}", retryable=False)
        return None

    #: Statuses that mean "the gateway itself is not ready", not "the model said no".
    #: magpie is a *local* sidecar and it restarts its plugin host whenever it compares
    #: `@latest` against the versions installed (measured 2026-11-14: five plugins
    #: upgraded at 19:50:43, host restarted 19:50:47). For those seconds it would answer
    #: 502/503/504, and passing a 502 through unchanged is a lie that costs real time:
    #: the desktop client reads "bad gateway", retries 3 times inside 9 seconds — all of
    #: it inside the restart window — then reports the leg as broken, while the catalogue
    #: stayed empty for 3 m 46 s. 503 is what those statuses actually mean here.
    _NOT_READY_STATUSES = frozenset({502, 503, 504})
    #: Bounded so a restarting sidecar is retried soon; `Scheduler._apply_error` doubles it
    #: per consecutive failure, which is the right shape for a gap measured in seconds.
    NOT_READY_COOLDOWN = 2.0

    def _classify_upstream(self, status: int, oai_req: dict) -> UpstreamError | None:
        """Map a gateway status to what it *means*, not just what it says."""
        if status in self._NOT_READY_STATUSES:
            return UpstreamError(
                503,
                f"magpie: the local gateway is not ready (it answered {status}) — it is "
                "most likely restarting its plugin host; retry in a moment",
                retryable=True,
                cooldown=self.NOT_READY_COOLDOWN,
            )
        return self._classify_http(status) or self._not_served(status, oai_req)

    def _unreachable(self, err: Exception) -> UpstreamError:
        """Nothing on that port: not running yet, or the sidecar is being restarted."""
        return UpstreamError(
            503,
            f"magpie: the local gateway is unreachable ({type(err).__name__}) — it is not "
            "running or still starting; retry in a moment",
            retryable=True,
            cooldown=self.NOT_READY_COOLDOWN,
        )

    @contextlib.asynccontextmanager
    async def _open_stream(self, body: dict) -> AsyncIterator[httpx.Response]:
        try:
            async with self.http.stream(
                "POST",
                self._base_url() + CHAT_PATH,
                headers=self._headers(),
                json=body,
                timeout=self.timeout,
            ) as res:
                yield res
        except httpx.HTTPError as err:
            raise self._unreachable(err) from err

    async def chat(self, acc: Account, oai_req: dict) -> dict:
        body = self._body(oai_req, stream=False)
        try:
            res = await self.http.post(
                self._base_url() + CHAT_PATH,
                headers=self._headers(),
                json=body,
                timeout=self.timeout,
            )
        except httpx.HTTPError as err:
            raise self._unreachable(err) from err
        err = self._classify_upstream(res.status_code, oai_req)
        if err is not None:
            raise err
        try:
            out = res.json()
        except Exception as err:
            # 200 而 body 不成 JSON：网关把一手流（`text/event-stream`）当成了非流式答复，
            # 或者它重启插件主机时先回了 200 再断流。这是"未就绪"，不是"坏网关"——
            # 报 502 只会让客户端以为要退避很久，于是把同一个请求重烧一遍预算。
            _log.warning(
                "magpie: %s answered 200 with a body that is not JSON "
                "(model=%r, messages=%d, content-type=%r, first400=%r)",
                CHAT_PATH,
                oai_req.get("model"),
                len(oai_req.get("messages") or ()),
                res.headers.get("content-type"),
                (res.text or "")[:400],
            )
            raise UpstreamError(
                503,
                "magpie: the gateway answered with a body that is not JSON "
                f"({res.status_code}, {res.headers.get('content-type')!r}) — it is not ready; retry in a moment",
                retryable=True,
                cooldown=self.NOT_READY_COOLDOWN,
            ) from err
        if not isinstance(out, dict):
            _log.warning(
                "magpie: %s answered with a non-object body (model=%r, type=%s, first400=%r)",
                CHAT_PATH,
                oai_req.get("model"),
                type(out).__name__,
                repr(out)[:400],
            )
            raise UpstreamError(
                503,
                "magpie: the gateway answered with a body that is not an object — "
                "it is not ready; retry in a moment",
                retryable=True,
                cooldown=self.NOT_READY_COOLDOWN,
            )
        out["model"] = oai_req.get("model") or out.get("model")
        return out

    async def chat_stream(self, acc: Account, oai_req: dict) -> AsyncIterator[dict]:
        body = self._body(oai_req, stream=True)
        async with self._open_stream(body) as res:
            err = self._classify_upstream(res.status_code, oai_req)
            if err is not None:
                raise err
            async for line in res.aiter_lines():
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                if not isinstance(chunk, dict):
                    continue
                chunk["model"] = oai_req.get("model") or chunk.get("model")
                yield chunk


class MagpieProviderLeg(MagpieProvider):
    """One of magpie's providers, as one of our legs.

    magpie's own model ids are ``<provider>/<model>`` — the provider is the
    first segment. Publishing them **verbatim** means three things fall out for
    free: the desktop groups them the way magpie groups them (each vendor under
    its own name, never one "MAGPIE" bucket), our routing needs no translation
    table, and a provider magpie adds becomes a card and a group without a line
    of ours changing (user, 11-09 20:3x: 「你的卡片列表可以按它的列表来维护了」).

    The card is the vendor's, not magpie's: the user signs in to WorkBuddy, and
    the sign-in lives behind magpie (user, 11-09 20:0x: 「前端还是用我们自己的
    操作逻辑。要配置 workbuddy 就要在相应的卡片点击加号配置的」).
    """

    # A leg does own the catalogue for its own vendor, unlike the umbrella.
    publishes_models = True

    def __init__(
        self,
        config: dict,
        http: httpx.AsyncClient,
        spec: dict,
        umbrella: MagpieProvider | None = None,
    ):
        super().__init__(config, http)
        self.spec = dict(spec)
        #: **Our** id — the card id, the model id prefix, the preset key. It does
        #: not change when magpie renames the provider upstream.
        self.provider_id = str(self.spec.get("id") or "").strip()
        #: Their id — what we send them. Renaming is a one-line change in our table.
        self.upstream_id = str(self.spec.get("upstream") or self.provider_id).strip()
        #: The leg id IS our id, so the desktop's row identity and the model id
        #: prefix are the same word.
        self.name = self.provider_id or self.name
        self.display_name = str(self.spec.get("name") or self.name)
        #: The umbrella leg: one catalogue read serves every provider leg.
        self.umbrella = umbrella

    # --- the card ----------------------------------------------------------
    def present(self) -> bool:
        """This one **is** a card: the product behind it is what the user sets up."""
        return bool(self.spec.get("card", True))

    def card_spec(self) -> dict:
        """The card's own fields, from **our** table.

        ``login`` is the one the drawer already knows how to speak: ours says
        which lane we use (oauth = browser, api = a key). We do not invent a
        method magpie did not offer.
        """
        login = str(self.spec.get("login") or "oauth")
        # 「装没装」不能只听构建那一刻的结论：插件随时会被 `magpie plugin rm` 卸掉，
        # 而它们的名册特意留着条目（账号保留），于是 present 永远答「在」。这里每次
        # 问一遍插件清单（umbrella 那份带 TTL 的缓存兜着开销），卡上才有真话：
        # 卸载后卡片留着、控件仍是那个加号（用户 11-21：「就按 A，插件没了点配置
        # 自动装回来」）：加号打开的就是本卡面板，面板头一件事替用户跑
        # install_command 把插件装回来，装完接着落登录 —— 没有额外的「装回来」按钮。
        plugin = str(self.spec.get("plugin") or "").strip() or None
        installed = self.spec.get("installed")
        if plugin is not None:
            source = self.umbrella if self.umbrella is not None else self
            with contextlib.suppress(Exception):
                loaded = source._installed_plugins()
                if loaded is not None:
                    installed = their_list.bare_spec(plugin) in loaded
        command = f"magpie plugin add {plugin}" if plugin and installed is False else None
        return {
            "name": self.display_name,
            "kind": "app",
            "login": login,
            "probe": {"probe": "none"},
            "download": None,
            # From the spec, not from our own id: a vendor we named keeps its
            # four-locale label even if our id and theirs ever drift apart.
            "intro_key": str(self.spec.get("intro_key") or self.provider_id),
            # What the card needs to put the plugin back by itself.
            "installed": installed,
            "plugin": plugin,
            "install_command": command,
            "install": {"kind": "plugin", "command": command} if command else None,
        }

    def _login_method(self) -> str:
        """Which of magpie's own sign-in methods we take, named by us.

        `magpie plugin login <provider>` **without** a method asks: it prints
        a menu on the terminal. Read as a pipe — which is how the gateway runs
        it — that menu is never drawn and the command waits forever (measured
        2026-11-10: the card spun and the session came back `failed` with the
        menu's tail as its detail, 「3. WorkBuddy API key；>」).

        Their command takes the method as its 1-based number, its label or its
        type (`oauth`/`api`) ⇒ we name the browser one ourselves, out of the
        merged roster — the method labels only ever arrive on the CLI's stream,
        magpie's on-disk roster does not carry them.
        """
        source = self.umbrella if self.umbrella is not None else self
        row = (source._their_providers() or {}).get(self.upstream_id) or {}
        methods = [m for m in (row.get("methods") or []) if isinstance(m, dict)]
        oauth = [m for m in methods if str(m.get("type") or "") == "oauth"]
        for method in oauth:
            label = str(method.get("label") or "")
            if "browser" in label.lower() or "浏览器" in label:
                return label
        if oauth:
            return str(oauth[0].get("label") or "oauth")
        return ""

    def login_plan(self) -> dict:
        """Signing in happens behind magpie: we run **its** login command.

        The drawer's shape and wording are ours and do not change — the button
        says 「开始登录」, it waits, and the switch appears when the catalogue
        says so. What runs behind it is magpie's own `plugin login`, which opens
        the browser itself (that is where the account is signed in — the same
        credential the provider's own app would hold).

        **The method is named by us**: without it their command asks on the
        terminal, and a pipe never sees the menu ⇒ it waits forever (measured
        2026-11-10). With it, no menu, no console window, and the browser
        opens by itself.

        `tty` is only for the one case a terminal is really needed: a provider
        whose only sign-in is a key (zen-free's), which the user has to type.
        """
        binary = ""
        umbrella = self.umbrella
        if umbrella is not None:
            binary = umbrella._binary() or ""
        method = self._login_method()
        argv = ["plugin", "login", self.upstream_id]
        if method:
            argv.append(method)
        return {
            "kind": "cli_login",
            "name": self.display_name,
            "binary": binary,
            "argv": argv,
            "tty": not method,
            "reason": f"它的登录在 Magpie 那边 {self.display_name}",
        }

    def signed_in(self) -> tuple[bool, str]:
        """Has *this* provider an account in magpie yet?

        The gateway being up is not the same question: magpie runs long before
        its providers are signed in, and a card that says 「已连接」 while the
        vendor has no account is a lie (the user's own rule, 11-07).
        """
        if self.served_models:
            return True, "ok"
        if not self._gateway_up():
            return False, "Magpie 没起来"
        return False, "它还没在 Magpie 那边登录"

    def _gateway_up(self) -> bool:
        """Is magpie itself answering right now?"""
        live, _reason = MagpieProvider.credential_health(self)
        return live

    def credential_health(self) -> tuple[bool, str]:
        """Can this provider serve a turn right now?

        Only if magpie is up **and** this provider is signed in — the first is
        the gateway, the second is the account.
        """
        if not self._gateway_up():
            return False, "no_desktop_credential"
        if not self.served_models:
            return False, "no_desktop_credential"
        return True, "ok"

    # --- the catalogue -----------------------------------------------------
    async def refresh_models(self, acc: Account) -> list[str] | None:
        """This provider's models, read from magpie's own catalogue.

        Their rows carry **their** provider id; we publish **ours** in front of
        the model name, so a rename upstream never changes the ids the user's
        presets and sessions hold.
        """
        reader = self.umbrella or self
        rows = await reader.read_catalogue()
        if not rows:
            return None
        mine: dict[str, dict] = {}
        for mid, row in rows.items():
            upstream, _, model = mid.partition("/")
            if upstream != self.upstream_id or not model:
                continue
            mine[f"{self.provider_id}/{model}"] = row
        if not mine:
            return None
        self._model_rows = mine
        self.served_models = tuple(mine)
        self._health = (True, "ok", time.time())
        return list(mine)

    def provider_of(self, model: str) -> str:
        """The vendor behind a model — the group the picker lists it under."""
        return self.provider_id

    def _map_model(self, model: str) -> str:
        """Our id → theirs: the only place the two vocabularies meet."""
        name = str(model or "")
        prefix = f"{self.provider_id}/"
        if name.startswith(prefix):
            return f"{self.upstream_id}/{name[len(prefix):]}"
        return name
