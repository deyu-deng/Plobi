"""One-command gate for the model picker's contract (user 2026-10-27/28).

The promise: after a restart the chat model box lists only models that can actually
serve a turn, under names the upstream really uses, and nothing for legs the user never
configured. Four layers decide that, and this script reads every one of them the way
the desktop does — it starts nothing, sends no completion, and spends no quota:

  1. the hub's own verdict      `GET /v1/models?selectable=1` (live AND tools-bearing)
  2. what the picker receives   `model.options` with `explicit_only=true` (the desktop's
                                exact arguments, through `plobi_cli.inventory`)
  3. the row filter             the same rule as `quotaProviders()` in the renderer:
                                a row with `authenticated === false` is dropped
  4. the default selection      the payload's `provider` / `model` must resolve to a
                                usable pair, not to a leg nobody signed into

Exit code 0 = every layer agrees; 1 = at least one layer disagrees (the line says which).

Run (hub must be up on the configured port; the desktop starts it at boot):
    cd Code && .venv/Scripts/python.exe quota_hub/tools/check_picker.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plobi.agents.registry import aigw_api_key, aigw_base_url  # noqa: E402
from plobi_cli import models as model_catalog  # noqa: E402
from plobi_cli.inventory import build_models_payload, load_picker_context  # noqa: E402

FAILURES: list[str] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    print(("PASS  " if ok else "FAIL  ") + label + (f" — {detail}" if detail else ""))
    if not ok:
        FAILURES.append(label)


def main() -> int:
    base = aigw_base_url()
    key = (aigw_api_key() or "").strip()
    print(f"hub: {base}")

    selectable = model_catalog.hub_selectable_model_ids(key, base)
    if selectable is None:
        check(False, "hub answers its selectable view", "no answer — is the local hub running?")
        return 1
    check(bool(selectable), "hub offers at least one usable model", f"{len(selectable)} ids")
    check(len(set(selectable)) == len(selectable), "hub offers no duplicate id")

    catalog = model_catalog.provider_model_ids("aigw")
    check(
        sorted(catalog) == sorted(selectable),
        "the model catalog equals the hub's selectable view",
        f"catalog={len(catalog)} selectable={len(selectable)}",
    )

    payload = build_models_payload(
        load_picker_context(),
        explicit_only=True,
        include_unconfigured=False,
        picker_hints=True,
        canonical_order=True,
        pricing=True,
        capabilities=True,
        refresh=False,
        probe_custom_providers=False,
        probe_current_custom_provider=True,
    )
    rows = payload.get("providers") or []
    print(f"picker payload: {len(rows)} row(s)")
    for row in rows:
        slug = row.get("slug") or row.get("id") or "?"
        print(f"  {slug:<16} models={len(row.get('models') or []):<3} authenticated={row.get('authenticated')}")

    # Layer 3 — `quotaProviders()` drops a row whose credential was never there.
    unusable = [row.get("slug") or row.get("id") for row in rows if row.get("authenticated") is False]
    picker_rows = [row for row in rows if row.get("authenticated") is not False]
    check(
        not unusable,
        "no row reaches the picker without a credential",
        f"dropped by the renderer filter: {unusable}" if unusable else "",
    )
    check(bool(picker_rows), "the picker keeps at least one row")

    hub_row = next((row for row in picker_rows if (row.get("slug") or "") == "aigw"), None)
    check(hub_row is not None, "the quota hub has a row in the picker")
    if hub_row is not None:
        names = hub_row.get("models") or []
        check(
            sorted(names) == sorted(selectable),
            "the hub row lists exactly the selectable models",
            f"row={len(names)} selectable={len(selectable)}",
        )

    provider = (payload.get("provider") or "").strip()
    model = (payload.get("model") or "").strip()
    check(bool(provider and model), "the default selection resolves", f"{provider} / {model}")
    check(
        provider != "aigw" or model in set(selectable),
        "the default selection is usable",
        f"{provider} / {model}",
    )

    print()
    if FAILURES:
        print(f"RED — {len(FAILURES)} layer(s) disagree: " + ", ".join(FAILURES))
        return 1
    print(f"GREEN — {len(selectable)} usable model(s), names from the hub, nothing unconfigured")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
