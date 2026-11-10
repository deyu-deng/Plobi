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

import json
import os
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx

from ...base import Account, Capabilities, Credential, Provider, UpstreamError
from . import their_list

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
    #: Their HTTP surface has **no** endpoint for the provider list — it serves
    #: models and turns, and nothing else about the roster. The roster lives in
    #: the plugin host, and it reaches us two ways: the host's own table on disk
    #: (`plugin-providers.json`, see `their_list`) and the CLI that prints it.
    #: The file is cheap and read first; the CLI boots their plugin host, so it
    #: is the fallback and it is trusted for much longer.
    THEIR_IDS_TTL_SECONDS = 60.0
    THEIR_IDS_CLI_TTL_SECONDS = 600.0

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
        """The providers we know about — **our** table, checked against theirs.

        Our table (``providers.yaml``) is the truth about what a card is called,
        how we sign in, and which id we publish. Their CLI only answers two
        questions: *is this provider still there* and *what is it called today*.
        A provider they added that we have not claimed gets **no card** — it is
        reported (``unclaimed_providers``) so a human can claim it in one line.

        When their CLI cannot be read at all (magpie is not running here), the
        cards still appear: our table does not depend on them. They then say
        「Magpie 没起来」 instead of pretending the vendor is not set up.
        """
        table = self._our_table()
        theirs = self._their_provider_ids()
        specs: list[dict] = []
        for ours, row in table.items():
            upstream = str(row.get("upstream") or ours)
            specs.append(
                {
                    "id": ours,
                    "upstream": upstream,
                    "name": str(row.get("name") or ours),
                    "card": bool(row.get("card", True)),
                    "login": str(row.get("login") or "oauth"),
                    # They have it? (None = we could not ask — say so, do not guess.)
                    "present": None if theirs is None else upstream in theirs,
                }
            )
        return specs

    def _our_table(self) -> dict[str, dict]:
        """Our provider table, read once per process."""
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
            # No table, no providers: we never invent one out of their JSON.
            table = {}
        self._table_cache = table
        return table

    def _their_provider_ids(self) -> set[str] | None:
        """The provider ids magpie has today, or None when we cannot ask.

        Their own table first: the plugin host writes ``plugin-providers.json``
        next to the rest of its data, and we already know that directory (the
        desktop pins ``XDG_CONFIG_HOME`` to our own volume). Their CLI is the
        fallback — it boots their plugin host, which is slow, and a plugin that
        cannot load can stop it from printing the table at all.

        ``None`` means "we could not ask" and it is a real answer: the cards
        then stay, because our table decides the card list and their roster
        only says whether a vendor is still theirs.
        """
        cached = getattr(self, "_their_ids_cache", None)
        now = time.time()
        if cached is not None and now - cached[1] < self.THEIR_IDS_TTL_SECONDS:
            return cached[0]
        ids = self._their_ids_from_their_table()
        if ids is None:
            ids = self._their_ids_via_cli()
        self._their_ids_cache = (ids, now)
        return ids

    def _their_ids_from_their_table(self) -> set[str] | None:
        """The ids in the plugin host's own table, or None when there is none."""
        table = their_list.read_provider_table(self._config_root())
        if table is None:
            return None
        ids = their_list.ids_from_table(table)
        # An empty table is "we read it and it says nothing" — not "they have
        # no providers". A machine whose plugin host has not loaded yet must
        # not lose every card.
        return ids or None

    def _their_ids_via_cli(self) -> set[str] | None:
        """Run their CLI once and read the provider ids out of its table.

        Their CLI prints one line per provider — ``workbuddy-ai (WorkBuddy AI,
        16 models)  not signed in`` — and their host's logs share the stream,
        so only the lines with that shape are read. (``plugin list --json``
        answers ``providers: null`` unless the host is up, which is exactly
        when we need it.)
        """
        cached = getattr(self, "_their_cli_ids_cache", None)
        now = time.time()
        if cached is not None and now - cached[1] < self.THEIR_IDS_CLI_TTL_SECONDS:
            return cached[0]
        binary = self._binary()
        ids = None
        if binary:
            ids = their_list.cli_provider_ids(
                binary,
                env={**os.environ, "DO_NOT_TRACK": "1"},
                timeout=120.0,
            )
        self._their_cli_ids_cache = (ids, now)
        return ids

    def unclaimed_providers(self) -> list[dict]:
        """Providers magpie has that our table does not claim — nobody's business yet.

        They are reported (log line + ``check_magpie_providers.py``) and they get
        **no card**: a provider we have not named must not show up on the user's
        screen just because somebody upstream added it.
        """
        theirs = self._their_provider_ids()
        if not theirs:
            return []
        ours = set(self._our_table())
        unknown: list[dict] = []
        for pid in sorted(theirs - ours):
            unknown.append({"id": pid})
        return unknown

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

    async def chat(self, acc: Account, oai_req: dict) -> dict:
        body = self._body(oai_req, stream=False)
        res = await self.http.post(
            self._base_url() + CHAT_PATH,
            headers=self._headers(),
            json=body,
            timeout=self.timeout,
        )
        err = self._classify_http(res.status_code) or self._not_served(res.status_code, oai_req)
        if err is not None:
            raise err
        try:
            out = res.json()
        except Exception as err:
            raise UpstreamError(
                502, "magpie: the gateway answered with a body that is not JSON", retryable=True
            ) from err
        if not isinstance(out, dict):
            raise UpstreamError(
                502, "magpie: the gateway answered with a body that is not an object", retryable=True
            )
        out["model"] = oai_req.get("model") or out.get("model")
        return out

    async def chat_stream(self, acc: Account, oai_req: dict) -> AsyncIterator[dict]:
        body = self._body(oai_req, stream=True)
        async with self.http.stream(
            "POST",
            self._base_url() + CHAT_PATH,
            headers=self._headers(),
            json=body,
            timeout=self.timeout,
        ) as res:
            err = self._classify_http(res.status_code) or self._not_served(res.status_code, oai_req)
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
        return {
            "name": self.display_name,
            "kind": "app",
            "login": login,
            "probe": {"probe": "none"},
            "download": None,
            "intro_key": self.provider_id,
        }

    def _login_method(self) -> str:
        """Which of magpie's own sign-in methods we take, named by us.

        `magpie plugin login <provider>` **without** a method asks: it prints
        a menu on the terminal. Read as a pipe — which is how the gateway runs
        it — that menu is never drawn and the command waits forever (measured
        2026-11-10: the card spun and the session came back `failed` with the
        menu's tail as its detail, 「3. WorkBuddy API key；>」).

        Their command takes the method as its 1-based number, its label or its
        type (`oauth`/`api`) ⇒ we name the browser one ourselves, from their
        own provider table (`plugin-providers.json`, the same file their
        roster comes from).
        """
        table = their_list.read_provider_table(self._config_root()) or {}
        row = table.get(self.upstream_id) or {}
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
