"""Which providers magpie has today — three sources, merged, never guessed.

magpie keeps its roster in more than one place, and no single one of them is the
roster:

1. ``GET /v1/models`` — the live catalogue. Every id is ``<provider>/<model>``,
   so the prefixes *are* the providers that can serve right now. Offline this
   answers nothing, and it is the only source with a real contract.
2. ``magpie plugin list`` — the plugin host's printed table. This is the only
   source that carries what the catalogue cannot: the vendor's own display name
   and its sign-in methods. A **plugin** row answers ``provides=[a, b]`` (it is
   one npm package that registers several providers); a **provider** row carries
   ``name=`` and ``methods=[...]``. Current shape (0.1.36xx)::

       - antigravity: spec=pi-antigravity@latest, name=Antigravity, methods=[{type:oauth, label:Sign in with Antigravity}], signedIn=false
       - qoder: spec=@magpie-community/opencode-qoder-auth@latest, provides=[qoder, qoder-cn], all not signed in
       - opencode-gemini-auth: spec=opencode-gemini-auth@latest, error: the plugin's load didn't finish in 20s

   Releases before 11-20 printed a different shape and that one is still read,
   because a machine may be a few releases behind::

       workbuddy-ai (WorkBuddy AI, 16 models)  not signed in

3. ``providers-state.json`` — magpie's own on-disk roster. It is the only source
   that knows the **built-in** providers: WorkBuddy is one (18 models, ``ready``,
   no plugin row anywhere), so a merge that skips this file silently drops a
   vendor the user is actually paying for.

None of these shapes is a contract we may lean on — magpie ships several
releases a day — so every reader is defensive: anything unrecognised yields
``None`` ("we could not ask") and never a guess. The card list is built from
whatever the merge finds; an entry we have no cosmetics for still gets a card,
under the name its vendor reported.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

#: magpie's on-disk roster: built-in providers plus the ones the user switched on.
STATE_FILE = "providers-state.json"

#: The older per-plugin table. Kept because a lagging release may still write it.
LEGACY_FILE = "plugin-providers.json"

#: The plugin list proper: one ``{"spec": ...}`` per installed plugin. Removing a
#: plugin drops its entry here while ``plugin-providers.json`` keeps its providers,
#: which is exactly the line we need to draw between "remembered" and "here".
PLUGINS_FILE = "plugins.json"

#: The roster carried by the plugin set we ship (``resources/magpie-plugins``).
#: Same name and same shape as ``LEGACY_FILE`` — the build copies their roster
#: in — but a different question: this one answers for providers **nothing on
#: this machine** has installed yet, which is what a card list needs.
PACKAGED_ROSTER_FILE = "plugin-providers.json"

#: Current CLI row: ``- <id>: key=value, key=value``. The vendor's ``methods``
#: value is bracketed and holds commas of its own, so splitting has to respect
#: bracket depth (see ``_split_fields``).
_CLI_ROW = re.compile(r"^\s*-\s+([A-Za-z0-9_.@-]+)\s*:\s*(.*)$")

#: Releases before 11-20: ``id (Name, N models)  ...``. The name may itself carry
#: brackets (``Grok (SuperGrok), 1 models``), so the shape is matched from the
#: *end* — the model count is what closes it.
_CLI_ROW_LEGACY = re.compile(r"^\s*([A-Za-z0-9_.@-]+)\s+\((.*),\s*(\d+)\s+models?\)")

#: One ``{type:oauth, label:Sign in with X}`` inside a ``methods=[...]`` value.
_METHOD_BLOCK = re.compile(r"\{([^{}]*)\}")

#: A plugin header in ``magpie plugin list``: ``<spec>  on`` — no indent, no
#: colon, no parentheses. It is the only line that says which plugins are
#: loaded right now, as opposed to which ones once registered a provider and
#: left its account behind after the plugin was removed.
_PLUGIN_LINE = re.compile(r"^(\S+)\s+on\s*$")


def _kv(item: str) -> tuple[str, str]:
    """``key=value`` / ``key: value`` -> ``(key, value)``; a bare word -> ``(word, "")``.

    Their printer uses ``=`` for most fields but ``:`` for the failure banner
    (``error: the plugin's load didn't finish in 20s``), so both are accepted.
    """
    cuts = [i for i in (item.find("="), item.find(":")) if i > 0]
    if not cuts:
        return item.strip(), ""
    cut = min(cuts)
    return item[:cut].strip(), item[cut + 1 :].strip()


def _split_fields(body: str) -> list[tuple[str, str]]:
    """Split a CLI row body on the commas that are *not* inside brackets."""
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    for ch in body:
        if ch in "[{(":
            depth += 1
        elif ch in "]})":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    if buf:
        parts.append("".join(buf))
    return [_kv(p) for p in parts if p.strip()]


def _unbracket(value: str) -> str:
    """``[a, b]`` -> ``a, b``; anything else unchanged."""
    text = (value or "").strip()
    if len(text) >= 2 and text[0] in "[(" and text[-1] in "])":
        return text[1:-1]
    return text


def _csv(value: str) -> list[str]:
    """``[qoder, qoder-cn]`` -> ``["qoder", "qoder-cn"]``."""
    return [p.strip().strip("\"'") for p in _unbracket(value).split(",") if p.strip()]


def _methods(value: str) -> list[dict]:
    """``[{type:oauth, label:Sign in with X}]`` -> ``[{"type": "oauth", "label": ...}]``."""
    found: list[dict] = []
    for block in _METHOD_BLOCK.findall(_unbracket(value)):
        row = {k.lower(): v for k, v in (_kv(p) for p in block.split(",")) if k}
        if row:
            found.append(row)
    return found


def _as_bool(text: str) -> bool | None:
    """Their ``true``/``false``, or None when the field says something else."""
    lowered = (text or "").strip().lower()
    if lowered in {"true", "yes", "signed in"}:
        return True
    if lowered in {"false", "no", "not signed in"}:
        return False
    return None


def _rows_from_payload(raw) -> dict[str, dict] | None:
    """Pull ``[{id: ...}]`` out of either on-disk shape.

    ``providers-state.json`` nests it as ``{"providers": {"providers": [...]}}``;
    the legacy file is the bare list. ``None`` means the shape is not one we know
    — an empty dict means we read it and it lists nothing.
    """
    if isinstance(raw, list):
        rows = raw
    elif isinstance(raw, dict):
        block = raw.get("providers")
        rows = block.get("providers") if isinstance(block, dict) else block
        if not isinstance(rows, list):
            return None
    else:
        return None
    table: dict[str, dict] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        pid = str(row.get("id") or "").strip()
        if pid:
            table[pid] = row
    return table


def read_provider_table(config_root: Path | str) -> dict[str, dict] | None:
    """magpie's on-disk roster, keyed by id — or None when neither file is readable.

    Tries ``providers-state.json`` first (it holds the built-in providers the
    plugin host never lists), then the older ``plugin-providers.json``.
    """
    base = Path(config_root) / "magpie"
    for name in (STATE_FILE, LEGACY_FILE):
        try:
            raw = json.loads((base / name).read_text(encoding="utf-8"))
        except Exception:
            continue
        table = _rows_from_payload(raw)
        if table is not None:
            return table
    return None


def read_installed_plugins(config_root: Path | str) -> set[str] | None:
    """The plugin specs magpie says are installed — bare, without the version tag.

    ``None`` means the file is missing or not a shape we know; the caller then
    falls back to asking the CLI. Both files matter: this one is the install
    list, ``plugin-providers.json`` is the provider roster that outlives it.
    """
    base = Path(config_root) / "magpie"
    try:
        raw = json.loads((base / PLUGINS_FILE).read_text(encoding="utf-8"))
    except Exception:
        return None
    items = raw.get("plugins") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        return None
    found: set[str] = set()
    for item in items:
        spec = item.get("spec") if isinstance(item, dict) else item
        if isinstance(spec, str) and spec.strip():
            found.add(bare_spec(spec))
    return found


def read_packaged_roster(root: Path | str) -> dict[str, dict] | None:
    """The roster that ships **with us** — ``<plugin set>/plugin-providers.json``.

    Same shape as ``LEGACY_FILE``, because it *is* that file: the build carries
    magpie's roster into the app with the per-machine fields stripped (no
    accounts, no ports, no paths). Which is the point — it names every provider
    the bundled plugins can serve, including the ones this machine has never
    installed a plugin for. ``None`` means the set has no readable roster.
    """
    try:
        raw = json.loads((Path(root) / PACKAGED_ROSTER_FILE).read_text(encoding="utf-8"))
    except Exception:
        return None
    return _rows_from_payload(raw)


def ids_from_table(table: dict[str, dict]) -> set[str]:
    """The provider ids in an on-disk roster."""
    return set(table)


def parse_cli_providers(text: str) -> dict[str, dict]:
    """The CLI's printed table, keyed by provider id.

    Both shapes are read off the same stream — their plugin host's logs share it,
    and a plugin that cannot load prints its own banner there. A row that reports
    a load failure is dropped, not counted as a provider.
    """
    rows: dict[str, dict] = {}
    for line in (text or "").splitlines():
        legacy = _CLI_ROW_LEGACY.match(line)
        if legacy:
            pid = legacy.group(1)
            row = rows.setdefault(pid, {"id": pid})
            row.setdefault("name", legacy.group(2).strip())
            row.setdefault("model_count", int(legacy.group(3)))
            continue

        match = _CLI_ROW.match(line)
        if not match:
            continue
        pid, body = match.group(1), match.group(2)
        fields = dict(_split_fields(body))

        # A plugin that failed to load is not a provider.
        if any(key.lower().startswith("error") for key in fields):
            continue

        provided = _csv(fields.get("provides", ""))
        if provided:
            # One npm package registering several providers: the ids are theirs,
            # the cosmetics (name, login) belong to each provider row or our table.
            for provider_id in provided:
                row = rows.setdefault(provider_id, {"id": provider_id})
                row.setdefault("plugin", pid)
                signed = _as_bool(fields.get("signedIn", ""))
                if signed is not None:
                    row.setdefault("signedIn", signed)
            continue

        row = rows.setdefault(pid, {"id": pid})
        row["plugin"] = pid
        if fields.get("name"):
            row["name"] = fields["name"]
        if fields.get("methods"):
            row["methods"] = _methods(fields["methods"])
        if fields.get("models"):
            row["models"] = _csv(fields["models"])
        signed = _as_bool(fields.get("signedIn", ""))
        if signed is not None:
            row["signedIn"] = signed
    return rows


def parse_cli_table(text: str) -> set[str]:
    """The provider ids in the CLI's printed table (either shape)."""
    return set(parse_cli_providers(text))


def cli_providers(binary: str, env: dict | None = None, timeout: float = 120.0) -> dict[str, dict] | None:
    """Ask their CLI once and read the provider rows out of its table.

    ``None`` means the CLI could not be run, or printed no row we recognise — the
    caller then says "we could not ask" instead of "they have none".
    """
    try:
        import subprocess  # noqa: PLC0415 - a sync call in a sync helper

        out = subprocess.run(
            [binary, "plugin", "list"],
            capture_output=True,
            timeout=timeout,
            env=env,
        )
    except Exception:
        return None
    rows = parse_cli_providers((out.stdout or b"").decode("utf-8", "replace"))
    return rows or None


def bare_spec(name: str) -> str:
    """``@scope/thing@latest`` -> ``@scope/thing``; ``pi-antigravity`` unchanged.

    Their CLI prints the spec with whatever tag is pinned; the on-disk table
    writes the same spec. Comparing the two verbatim would make a pilot-pinned
    plugin look uninstalled, so the tag goes before the comparison, not in it.
    """
    return name.rsplit("@", 1)[0].strip().lower() if "@" in name.lstrip("@") else name.strip().lower()


def parse_installed_plugins(text: str) -> set[str]:
    """The plugin specs ``magpie plugin list`` says are loaded right now.

    Removing a plugin leaves its providers listed in the on-disk roster (their
    accounts are kept on purpose), so the roster cannot answer "is this still
    installed". These header lines can: a removed plugin prints no header.
    """
    found: set[str] = set()
    for line in (text or "").splitlines():
        match = _PLUGIN_LINE.match(line)
        if match:
            found.add(bare_spec(match.group(1)))
    return found


def cli_installed_plugins(binary: str, env: dict | None = None, timeout: float = 120.0) -> set[str] | None:
    """Ask their CLI which plugins are loaded. ``None`` = we could not ask."""
    try:
        import subprocess  # noqa: PLC0415 - a sync call in a sync helper

        out = subprocess.run(
            [binary, "plugin", "list"],
            capture_output=True,
            timeout=timeout,
            env=env,
        )
    except Exception:
        return None
    found = parse_installed_plugins((out.stdout or b"").decode("utf-8", "replace"))
    return found or None


def cli_provider_ids(binary: str, env: dict | None = None, timeout: float = 120.0) -> set[str] | None:
    """Just the ids — kept for callers that do not need the cosmetics."""
    rows = cli_providers(binary, env=env, timeout=timeout)
    return set(rows) if rows else None


def providers_from_model_ids(model_ids: list[str]) -> dict[str, dict]:
    """``["workbuddy/hy4", "qoder/v3"]`` -> one row per provider prefix.

    The catalogue is the only *live* answer to "which providers can serve right
    now", so its prefixes seed the roster. It carries no cosmetics at all.
    """
    rows: dict[str, dict] = {}
    for model_id in model_ids or []:
        text = str(model_id or "").strip()
        if "/" not in text:
            continue
        prefix = text.split("/", 1)[0].strip()
        if prefix:
            rows.setdefault(prefix, {"id": prefix, "live": True})
    return rows
