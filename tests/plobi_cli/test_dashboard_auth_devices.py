"""The pairing store's own rules (WP-APP-PAIR, 裁定 64).

Nothing HTTP here — this is the layer that decides whether a code or a device
token is real, so every number in the docstring gets a test: one open window,
6 digits, 5 minutes, one use, five wrong guesses lock pairing for an hour, a
revoked device is gone on the next request, and no secret ever lands on disk in
plaintext.

The lockout counter gets the most attention because it is the *only* thing that
keeps a 6-digit code out of brute-force range, and its first implementation
stored the counter inside the same dict the expiry sweep walked — which deleted
the counter on every read, so the count never reached five. The sweep and the
counter are separate keys now, and ``test_wrong_guesses_accumulate_to_a_lockout``
is the regression pin.
"""

from __future__ import annotations

import json
import time

import pytest

from plobi_cli.dashboard_auth import devices as dev


@pytest.fixture
def store(tmp_path):
    return dev.DeviceStore(tmp_path)


def _codes(store) -> dict:
    return json.loads(store._codes_path.read_text(encoding="utf-8"))


def _not(code: str) -> str:
    """A 6-digit guess known to differ from ``code`` (which may be the live one)."""
    return "000000" if code != "000000" else "111111"


def _expire_open_code(store):
    """Backdate the open window's ``expires_at`` — the file is the source of truth."""
    data = _codes(store)
    data["active"]["expires_at"] = time.time() - 1
    store._codes_path.write_text(json.dumps(data), encoding="utf-8")


def _release_lockout(store):
    data = _codes(store)
    data["failed"]["locked_until"] = time.time() - 1
    store._codes_path.write_text(json.dumps(data), encoding="utf-8")


# ------------------------------------------------------------------ the window


def test_issue_code_opens_a_six_digit_window_once(store):
    code, expires_at = store.issue_code()
    assert len(code) == dev.CODE_DIGITS and code.isdigit()
    assert expires_at == pytest.approx(time.time() + dev.CODE_TTL_SECONDS, abs=2)
    assert store.pending_code_expires_at() == pytest.approx(expires_at, abs=1)


def test_a_second_code_kills_the_first(store):
    """One pairing window at a time — the desktop only ever shows one code."""
    first, _ = store.issue_code()
    second, _ = store.issue_code()
    assert store.redeem(first, name="handset") is None
    assert store.redeem(second, name="handset") is not None


def test_the_window_closes_on_its_own(store):
    code, _ = store.issue_code()
    _expire_open_code(store)
    assert store.pending_code_expires_at() == 0.0
    assert store.redeem(code, name="handset") is None


def test_redeem_is_single_use(store):
    code, _ = store.issue_code()
    assert store.redeem(code, name="handset") is not None
    assert store.redeem(code, name="handset") is None


# ---------------------------------------------------------------- the budget


def test_wrong_guesses_accumulate_to_a_lockout(store):
    """Five, then no more — for an hour, and including for the desktop."""
    code, _ = store.issue_code()
    bad = _not(code)
    for _ in range(dev.MAX_FAILED_REDEEM - 1):
        assert store.pairing_locked_until() == 0.0
        assert store.redeem(bad, name="handset") is None
    with pytest.raises(dev.PairingLocked):
        store.redeem(bad, name="handset")  # the fifth is itself refused
    assert store.pairing_locked_until() > time.time()
    with pytest.raises(dev.PairingLocked):
        store.issue_code()


def test_lockout_expires_and_pairing_reopens(store):
    code, _ = store.issue_code()
    bad = _not(code)
    for _ in range(dev.MAX_FAILED_REDEEM):
        try:
            store.redeem(bad, name="handset")
        except dev.PairingLocked:
            break
    _release_lockout(store)
    assert store.pairing_locked_until() == 0.0
    fresh, _ = store.issue_code()
    assert store.redeem(fresh, name="handset") is not None


def test_a_guess_with_no_window_open_is_not_charged(store):
    """A device fumbling after a code expired is the operator's annoyance.

    It must not be counted as an attack — one late call would otherwise eat a
    slot of the budget and lock pairing before anyone ever saw a code.
    """
    for _ in range(dev.MAX_FAILED_REDEEM):
        assert store.redeem("111111", name="handset") is None
    assert store.pairing_locked_until() == 0.0
    assert store.issue_code()[1] > time.time()


def test_a_redemption_resets_the_budget(store):
    first, _ = store.issue_code()
    for _ in range(dev.MAX_FAILED_REDEEM - 1):
        store.redeem(_not(first), name="handset")
    code, _ = store.issue_code()
    assert store.redeem(code, name="handset") is not None
    # Counter back to zero: the next five wrong guesses are a fresh story, not
    # the sixth of this one.
    assert store._failed_attempts()[0] == 0


# ------------------------------------------------------------------- devices


def test_token_verifies_the_device_that_minted_it(store):
    code, _ = store.issue_code()
    device_id, token = store.redeem(code, name="客厅平板")
    device = store.verify_token(token)
    assert device is not None
    assert device.device_id == device_id
    assert device.name == "客厅平板"
    assert store.verify_token("钥匙-not-a-token") is None
    assert store.verify_token("") is None


def test_revoke_ends_exactly_one_device(store):
    tokens = {}
    for name in ("phone", "tablet"):
        code, _ = store.issue_code()
        device_id, token = store.redeem(code, name=name)
        tokens[name] = (device_id, token)

    assert store.revoke(tokens["phone"][0]) is True
    assert store.verify_token(tokens["phone"][1]) is None
    assert store.verify_token(tokens["tablet"][1]).name == "tablet"
    assert [d.name for d in store.list_devices()] == ["tablet"]
    assert store.revoke(tokens["phone"][0]) is False  # idempotent: nothing left to end


def test_last_seen_does_not_hit_the_disk_on_every_request(store):
    code, _ = store.issue_code()
    _device_id, token = store.redeem(code, name="handset")
    before = store.list_devices()[0].last_seen_at
    for _ in range(3):
        store.verify_token(token)
    assert store.list_devices()[0].last_seen_at == before


# ------------------------------------------------------------------ at rest


def test_no_secret_is_written_in_plaintext(store):
    code, _ = store.issue_code()
    _device_id, token = store.redeem(code, name="handset")
    on_disk = (
        store._codes_path.read_text(encoding="utf-8")
        + store._devices_path.read_text(encoding="utf-8")
    )
    assert code not in on_disk
    assert token not in on_disk


def test_a_corrupt_store_trusts_nobody(store):
    """Fail closed: a file we can't parse is *no* devices, not an empty-but-OK one."""
    store._dir.mkdir(parents=True, exist_ok=True)
    store._devices_path.write_text("{not json", encoding="utf-8")
    store._codes_path.write_text("also not json", encoding="utf-8")
    assert store.list_devices() == []
    assert store.verify_token("anything") is None
    assert store.pending_code_expires_at() == 0.0
    code, _ = store.issue_code()
    assert store.redeem(code, name="handset") is not None


def test_a_parseable_but_bogus_record_is_refused_not_a_500(store):
    """An auth path answers 401, never 500 — including when the salt isn't hex.

    The JSON layer already fails closed on unparseable files; this is the same
    stance one level deeper, for a record that parses and simply isn't ours.
    """
    store._dir.mkdir(parents=True, exist_ok=True)
    store._devices_path.write_text(
        json.dumps({"dev-junk": {"name": "junk", "hash": "aa", "salt": "zz"}}),
        encoding="utf-8",
    )
    assert store.verify_token("whatever") is None

    store._codes_path.write_text(
        json.dumps(
            {
                "active": {"hash": "aa", "salt": "zz", "expires_at": time.time() + 60},
                "failed": {},
            }
        ),
        encoding="utf-8",
    )
    assert store.redeem("123456", name="handset") is None
    assert store._failed_attempts()[0] == 1  # a guess against a live window is charged


def test_root_writable_answers_yes_and_prepares_the_dir(store, tmp_path):
    """A fresh home is pairable, and checking it leaves the dir ready to write."""
    assert store.root_writable() is True
    assert (tmp_path / "plobi").is_dir()


def test_root_writable_answers_no_when_the_dir_path_is_taken_by_a_file(store, tmp_path):
    """The read-only-home case the LAN bind gate asks about, without chmod.

    ``<home>/plobi`` existing as a regular file makes the store's own ``mkdir``
    raise, which is the honest signal for "no device can ever be recorded here".
    A ``chmod`` probe would be a no-op on Windows and prove nothing there.
    """
    (tmp_path / "plobi").write_text("not a directory", encoding="utf-8")
    assert store.root_writable() is False
