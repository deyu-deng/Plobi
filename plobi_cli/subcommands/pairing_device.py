"""``plobi pairing device`` — the command-line half of handset pairing (WP-APP-PAIR).

Why this exists at all
----------------------
The desktop UI can already open a 6-digit code, list paired devices and revoke one
by one (裁定 65). Without a CLI there is no way to script that surface: no
headless verification, no "pair a device" from a terminal, and — the reason it
matters — **no way to prove that a revoke actually lands on the next request**
rather than just disappearing from a screen. Criterion three of ``WP-PAIR-UI`` is
exactly that proof, and it needs a token in hand.

Which store this talks to
-------------------------
This command never mints a credential of its own and never talks to a second
source of truth: it drives :class:`~plobi_cli.dashboard_auth.devices.DeviceStore`
under ``<PLOBI_HOME>/plobi`` — the *same* file the running backend reads on every
request. ``DeviceStore.verify_token`` re-reads the file per call (deliberately:
``revoke`` has to take effect on the device's next request, not on the next
restart), so pairing, listing and revoking here are immediately visible to a live
dashboard. Nothing here is cached, and nothing here is a shared secret.

The retired ``app_token`` stays retired (裁定 68): this command yields a
**per-device** token that can be revoked on its own. There is deliberately no
flag to produce a machine-wide credential — that was the design we removed, and
convenience is not a reason to re-add it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable, Optional


def _fmt_epoch(ts: float) -> str:
    """A human-readable local timestamp for a 0.0 'never' sentinel."""
    if not ts:
        return "never"
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def issue_and_redeem(
    *, name: str = "", store=None
) -> tuple[str, str, list[str]]:
    """Open a pairing code and redeem it right here, as one operator action.

    Returns ``(device_id, token, scopes)``. ``redeem`` is the same call the
    handset makes over HTTP, so a device created here is indistinguishable from
    one paired through the UI — same hashing, same salt, same single-use rule.

    Raises :class:`RuntimeError` when pairing is locked or the code did not
    produce a device: the code is single-use, so a failure here is never retried
    silently, and the guess budget (five wrong codes → one-hour lock) is exactly
    what a quiet retry would burn through.
    """
    from plobi_cli.dashboard_auth.devices import PairingLocked, get_store
    from plobi_cli.dashboard_auth.pairing import HANDSET_SCOPES

    store = store or get_store()
    try:
        code, _expires_at = store.issue_code()
    except PairingLocked as exc:
        raise RuntimeError(
            "pairing is locked after repeated failed attempts; "
            f"unlocks at {time_str(store.pairing_locked_until())}"
        ) from exc

    try:
        minted = store.redeem(code, name=name)
    except PairingLocked as exc:  # redeemed by someone else in between
        raise RuntimeError("pairing was locked before the code could be redeemed") from exc

    if minted is None:
        raise RuntimeError(
            "the code was rejected at redemption — this should be impossible for a "
            "freshly issued code in the same store; check the pairing files under "
            "$PLOBI_HOME/plobi"
        )
    device_id, token = minted
    return device_id, token, list(HANDSET_SCOPES)


def time_str(ts: float) -> str:
    return _fmt_epoch(ts)


def device_rows(store) -> list[str]:
    """One aligned line per paired device: id, name, paired at, last seen."""
    lines: list[str] = []
    for device in store.list_devices():
        lines.append(
            f"{device.device_id:<28} {device.name:<24} "
            f"paired {_fmt_epoch(device.paired_at):<21} last seen {_fmt_epoch(device.last_seen_at)}"
        )
    return lines


def pairing_state_lines(store) -> list[str]:
    """The two facts an operator needs before blaming the device: can we write,
    and is the guess budget currently spent?"""
    return [
        f"pairing store writable: {'yes' if store.root_writable() else 'NO — no device can pair into this home'}",
        f"devices paired: {len(store.list_devices())}",
        f"pairing locked until: {_fmt_epoch(store.pairing_locked_until())}",
        f"open code expires at: {_fmt_epoch(store.pending_code_expires_at())}",
    ]


def cmd_pairing_device(args) -> int:
    """Handler for ``plobi pairing device`` (parser wired in ``pairing.py``)."""
    from plobi_cli.dashboard_auth.devices import get_store

    store = get_store()

    # The HTTP surface caps a device name at 64 characters (``RedeemBody``); the CLI
    # has to refuse the same way, or a script could pair a device the desktop would
    # reject and the two paths would disagree about what a valid name is.
    if len(args.name) > 64:
        print(f"device name must be at most 64 characters (got {len(args.name)}) — nothing was paired")
        return 2

    if getattr(args, "revoke_device", ""):
        device_id = args.revoke_device
        if not store.revoke(device_id):
            print(f"no paired device with id {device_id!r} — nothing was revoked")
            return 1
        print(f"revoked {device_id}: its token stops verifying on that device's next request")
        return 0

    if getattr(args, "list_devices", False):
        rows = device_rows(store)
        print("\n".join(rows) if rows else "(no paired devices)")
        print("")
        print("\n".join(pairing_state_lines(store)))
        return 0

    device_id, token, scopes = issue_and_redeem(name=args.name, store=store)
    print(f"paired a device: {device_id}")
    print(f"  name   : {args.name or '(defaulted by the store)'}")
    print(f"  scopes : {', '.join(scopes)}")
    print(f"  token  : {token}")
    print("")
    print(
        "This token is shown once and is never stored in plaintext — the desktop keeps only its "
        "hash, so there is no recovery path. Revoke it later with:\n"
        f"    plobi pairing device --revoke {device_id}"
    )
    return 0


def build_pairing_device_parser(pairing_sub, *, handler: Optional[Callable] = None) -> None:
    """Attach ``plobi pairing device`` to the existing ``pairing`` subcommand tree."""
    device_parser = pairing_sub.add_parser(
        "device",
        help="Pair / list / revoke handsets (the CLI half of the desktop PAIRING page)",
        description=(
            "Drives the same device store the running backend reads on every request: "
            "open a pairing code and redeem it into this device's own token, list what is "
            "paired, or revoke one device so its next request is refused."
        ),
    )
    device_parser.add_argument("--name", default="", help="Device name shown in the desktop list (max 64)")
    device_parser.add_argument("--list", dest="list_devices", action="store_true",
                               help="List paired devices with paired/last-seen times")
    device_parser.add_argument("--revoke", dest="revoke_device", metavar="DEVICE_ID", default="",
                               help="Revoke one device by id; its token stops verifying immediately")
    device_parser.set_defaults(func=handler or cmd_pairing_device)
